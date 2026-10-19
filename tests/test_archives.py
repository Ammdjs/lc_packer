"""Behavior checks for untrusted MQB projects and APK templates."""

from __future__ import annotations

import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from lc_packer.project import ProjectError, read_project
from lc_packer.templates import TemplateError, inspect_template


SOURCE = b'Import "kp.luae"\r\nShowMessage "ok"'


def make_zip(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return path


def project_members() -> list[tuple[str, bytes]]:
    attachment = io.BytesIO()
    with zipfile.ZipFile(attachment, "w"):
        pass
    return [
        ("测试.mq", SOURCE),
        ("测试.prop", b"{}"),
        ("测试.uip", b"ui"),
        ("测试.atc", attachment.getvalue()),
    ]


def mark_first_member_encrypted(path: Path) -> None:
    data = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        location = data.index(signature)
        flags = struct.unpack_from("<H", data, location + flag_offset)[0]
        struct.pack_into("<H", data, location + flag_offset, flags | 1)
    path.write_bytes(data)


def mislabel_gb18030_filename_as_utf8(path: Path, gb18030_name: str = "测.mq") -> None:
    data = path.read_bytes()
    utf8 = "é.mq".encode("utf-8")
    gb18030 = gb18030_name.encode("gb18030")
    assert len(utf8) == len(gb18030)
    assert data.count(utf8) == 2  # Local header and central directory.
    path.write_bytes(data.replace(utf8, gb18030))


def clear_first_member_utf8_filename_flag(path: Path) -> None:
    data = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        location = data.index(signature)
        flags = struct.unpack_from("<H", data, location + flag_offset)[0]
        struct.pack_into("<H", data, location + flag_offset, flags & ~0x800)
    path.write_bytes(data)


class ProjectArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "input.mqb"

    def test_preserves_raw_resources_and_extracts_literal_import(self) -> None:
        make_zip(self.path, project_members())

        project = read_project(self.path)

        self.assertEqual(project.source_name, "测试.mq")
        self.assertEqual(project.source, SOURCE)
        self.assertEqual(project.properties, b"{}")
        self.assertEqual(project.ui, b"ui")
        self.assertEqual(project.attachments, project_members()[-1][1])
        self.assertEqual(project.dependencies, ("kp.luae",))
        with self.assertRaises((AttributeError, TypeError)):
            project.source = b"changed"

    def test_gb18030_source_scans_import_without_changing_bytes(self) -> None:
        source = 'Import "插件.luae"\r\nShowMessage "中文"'.encode("gb18030")
        make_zip(self.path, [("项目.mq", source)])

        project = read_project(self.path)

        self.assertEqual(project.source, source)
        self.assertEqual(project.dependencies, ("插件.luae",))

    def test_utf8_bom_source_scans_import(self) -> None:
        source = b'\xef\xbb\xbfImport "kp.luae"\n'
        make_zip(self.path, [("test.mq", source)])
        self.assertEqual(read_project(self.path).dependencies, ("kp.luae",))

    def test_reads_legacy_gb18030_name_with_incorrect_utf8_zip_flag(self) -> None:
        make_zip(self.path, [("é.mq", SOURCE)])
        mislabel_gb18030_filename_as_utf8(self.path)
        self.assertEqual(read_project(self.path).source_name, "测.mq")

    def test_default_mode_reads_unflagged_gb18030_member_name(self) -> None:
        make_zip(self.path, [("é.mq", SOURCE)])
        mislabel_gb18030_filename_as_utf8(self.path)
        clear_first_member_utf8_filename_flag(self.path)

        self.assertEqual(read_project(self.path).source_name, "测.mq")

    def test_explicit_gb18030_mode_recovers_ambiguous_legacy_name(self) -> None:
        make_zip(self.path, [("é.mq", SOURCE)])
        mislabel_gb18030_filename_as_utf8(self.path, "一.mq")

        self.assertEqual(read_project(self.path, filename_encoding="gb18030").source_name, "一.mq")

    def test_default_mode_preserves_valid_utf8_name_with_ambiguous_bytes(self) -> None:
        make_zip(self.path, [("һ.mq", SOURCE)])

        self.assertEqual(read_project(self.path).source_name, "һ.mq")

    def test_rejects_unknown_filename_encoding(self) -> None:
        make_zip(self.path, [("test.mq", SOURCE)])
        with self.assertRaises(ProjectError):
            read_project(self.path, filename_encoding="latin-1")

    def test_rejects_unsafe_member_names(self) -> None:
        unsafe = (
            "../outside.mq",
            "C:/outside.mq",
            "C:\\outside.mq",
            "\\\\server\\share\\outside.mq",
            "/absolute.mq",
            "..\\outside.mq",
            "folder/../outside.mq",
        )
        for name in unsafe:
            with self.subTest(name=name):
                make_zip(self.path, [(name, SOURCE)])
                with self.assertRaises(ProjectError):
                    read_project(self.path)

    def test_rejects_case_fold_duplicate_members(self) -> None:
        make_zip(self.path, [("test.mq", SOURCE), ("Assets/KP.LUAE", b"a"), ("assets/kp.luae", b"b")])
        with self.assertRaises(ProjectError):
            read_project(self.path)

    def test_rejects_multiple_sources(self) -> None:
        make_zip(self.path, [("first.mq", SOURCE), ("second.mq", SOURCE)])
        with self.assertRaises(ProjectError):
            read_project(self.path)

    def test_rejects_missing_source(self) -> None:
        make_zip(self.path, [("test.prop", b"{}")])
        with self.assertRaises(ProjectError):
            read_project(self.path)

    def test_rejects_unmatched_resource_stem(self) -> None:
        make_zip(self.path, [("test.mq", SOURCE), ("other.prop", b"{}")])
        with self.assertRaises(ProjectError):
            read_project(self.path)

    def test_rejects_declared_expanded_size_above_limit(self) -> None:
        make_zip(self.path, [("test.mq", SOURCE)])
        with patch("lc_packer.project.MAX_EXPANDED_BYTES", 8):
            with self.assertRaises(ProjectError):
                read_project(self.path)

    def test_rejects_entry_count_above_limit(self) -> None:
        make_zip(self.path, [("test.mq", SOURCE), ("note.txt", b"x")])
        with patch("lc_packer.project.MAX_ENTRIES", 1):
            with self.assertRaises(ProjectError):
                read_project(self.path)

    def test_rejects_encrypted_member(self) -> None:
        make_zip(self.path, [("test.mq", SOURCE)])
        mark_first_member_encrypted(self.path)
        with self.assertRaises(ProjectError):
            read_project(self.path)

    def test_rejects_import_paths(self) -> None:
        for dependency in ("../kp.luae", "folder/kp.luae", "folder\\kp.luae", "C:kp.luae"):
            with self.subTest(dependency=dependency):
                make_zip(self.path, [("test.mq", f'Import "{dependency}"'.encode())])
                with self.assertRaises(ProjectError):
                    read_project(self.path)


class TemplateArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "FreeApk.apkm"

    def test_inventory_reads_zip_content_irrespective_of_extension(self) -> None:
        make_zip(self.path, [
            ("AndroidManifest.xml", b"manifest"),
            ("classes.dex", b"dex"),
            ("assets/script.lc", b"eflc payload"),
            ("lib/x86/libmqm.so", b"so"),
        ])

        info = inspect_template(self.path)

        self.assertEqual(info.path, self.path)
        self.assertEqual(info.abis, ("x86",))
        self.assertEqual(info.script_assets, ("assets/script.lc",))
        self.assertEqual(info.payload_header, b"eflc")

    def test_rejects_missing_android_manifest(self) -> None:
        make_zip(self.path, [("classes.dex", b"dex")])
        with self.assertRaises(TemplateError):
            inspect_template(self.path)

    def test_rejects_missing_dex(self) -> None:
        make_zip(self.path, [("AndroidManifest.xml", b"manifest")])
        with self.assertRaises(TemplateError):
            inspect_template(self.path)

    def test_rejects_unsafe_member_even_when_template_is_otherwise_valid(self) -> None:
        make_zip(self.path, [
            ("AndroidManifest.xml", b"manifest"),
            ("classes.dex", b"dex"),
            ("../malicious", b"bad"),
        ])
        with self.assertRaises(TemplateError):
            inspect_template(self.path)


if __name__ == "__main__":
    unittest.main()
