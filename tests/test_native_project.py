"""Native assistant projects remain raw files with optional sibling resources."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lc_packer.project import MqbProject, ProjectError, read_project


class NativeProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "示例.mq"

    def test_reads_plain_mq_and_all_three_resources_without_changing_bytes(self) -> None:
        source = b'Dim example\r\nImport "demo.luae"\r\n'
        self.path.write_bytes(source)
        (self.root / "示例.prop").write_bytes(b'\xef\xbb\xbf{"name":"fixture"}\r\n')
        (self.root / "示例.uip").write_bytes(b"\x00\xffui\r\n")
        (self.root / "示例.atc").write_bytes(b"PK\x03\x04fake-attachment\x00")

        project = read_project(self.path)

        self.assertIsInstance(project, MqbProject)
        self.assertEqual(project.source_name, "示例.mq")
        self.assertEqual(project.source, source)
        self.assertEqual(project.properties, b'\xef\xbb\xbf{"name":"fixture"}\r\n')
        self.assertEqual(project.ui, b"\x00\xffui\r\n")
        self.assertEqual(project.attachments, b"PK\x03\x04fake-attachment\x00")
        self.assertEqual(project.dependencies, ("demo.luae",))

    def test_missing_resources_are_none_and_neighbor_projects_are_ignored(self) -> None:
        self.path.write_bytes(b"Dim example\n")
        (self.root / "neighbor.mq").write_bytes(b"not the selected project")
        (self.root / "neighbor.prop").write_bytes(b"neighbor properties")
        (self.root / "neighbor.uip").mkdir()
        (self.root / "示例.prop.backup").write_bytes(b"backup")
        (self.root / "nested").mkdir()
        (self.root / "nested" / "示例.atc").write_bytes(b"nested attachment")

        project = read_project(self.path)

        self.assertEqual(project.source, b"Dim example\n")
        self.assertIsNone(project.properties)
        self.assertIsNone(project.ui)
        self.assertIsNone(project.attachments)
        self.assertEqual(project.dependencies, ())

    def test_uppercase_extension_and_casefold_matching_sidecars_are_supported(self) -> None:
        self.path = self.root / "STRASSE.MQ"
        self.path.write_bytes(b"Dim fixture\n")
        (self.root / "Straße.PrOp").write_bytes(b"matched")
        (self.root / "strasse.UiP").write_bytes(b"ui")

        project = read_project(self.path)

        self.assertEqual(project.source_name, "STRASSE.MQ")
        self.assertEqual(project.properties, b"matched")
        self.assertEqual(project.ui, b"ui")

    def test_utf8_bom_and_gb18030_keep_raw_bytes_and_scan_imports(self) -> None:
        cases = (
            (b'\xef\xbb\xbfImport "demo.luae"\r\nDim fixture\r\n', ("demo.luae",)),
            ('Dim 示例\r\nImport "插件.luae"\r\nImport "插件.LUAE"\n'.encode("gb18030"), ("插件.luae",)),
        )
        for source, dependencies in cases:
            with self.subTest(dependencies=dependencies):
                self.path.write_bytes(source)
                project = read_project(self.path)
                self.assertEqual(project.source, source)
                self.assertEqual(project.dependencies, dependencies)

    def test_invalid_import_paths_are_rejected_with_actionable_chinese_error(self) -> None:
        self.path.write_bytes(b'Dim fixture\nImport "../outside.luae"\n')
        with self.assertRaisesRegex(ProjectError, "Import.*文件名"):
            read_project(self.path)

    def test_unsupported_encoding_is_rejected_with_actionable_chinese_error(self) -> None:
        self.path.write_bytes(b"\xff")
        with self.assertRaisesRegex(ProjectError, "编码.*UTF-8.*GB18030"):
            read_project(self.path)

    def test_missing_selected_source_has_actionable_chinese_error(self) -> None:
        with self.assertRaisesRegex(ProjectError, "读取.*示例[.]mq.*检查"):
            read_project(self.path)

    def test_matching_resource_directory_is_rejected(self) -> None:
        self.path.write_bytes(b"Dim fixture\n")
        (self.root / "示例.prop").mkdir()
        with self.assertRaisesRegex(ProjectError, "普通文件"):
            read_project(self.path)

    def test_selected_source_directory_is_rejected(self) -> None:
        self.path.mkdir()
        with self.assertRaisesRegex(ProjectError, "普通文件"):
            read_project(self.path)

    def test_ambiguous_casefold_resources_are_rejected(self) -> None:
        self.path = self.root / "strasse.mq"
        self.path.write_bytes(b"Dim fixture\n")
        first = self.root / "Straße.prop"
        second = self.root / "STRASSE.PROP"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        if first.samefile(second):
            self.skipTest("Filesystem does not distinguish these casefold-equivalent names")
        with self.assertRaisesRegex(ProjectError, "重名|歧义|多个"):
            read_project(self.path)

    def test_project_size_limit_counts_source_and_all_resources(self) -> None:
        self.path.write_bytes(b"Dim x\n")
        (self.root / "示例.prop").write_bytes(b"12345")
        (self.root / "示例.uip").write_bytes(b"12345")
        (self.root / "示例.atc").write_bytes(b"12345")
        with patch("lc_packer.project.MAX_EXPANDED_BYTES", 20):
            with self.assertRaisesRegex(ProjectError, "总大小.*限制"):
                read_project(self.path)

    def test_oversized_project_is_rejected_before_any_file_is_opened(self) -> None:
        self.path.write_bytes(b"Dim x\n")
        (self.root / "示例.atc").write_bytes(b"x" * 16)
        with patch("lc_packer.project.MAX_EXPANDED_BYTES", 16):
            with patch.object(Path, "open", side_effect=AssertionError("oversized content must not be read")):
                with self.assertRaisesRegex(ProjectError, "总大小.*限制"):
                    read_project(self.path)

    def test_exact_total_size_limit_is_accepted(self) -> None:
        self.path.write_bytes(b"Dim x\n")
        (self.root / "示例.prop").write_bytes(b"1234")
        with patch("lc_packer.project.MAX_EXPANDED_BYTES", 10):
            project = read_project(self.path)
        self.assertEqual(project.source, b"Dim x\n")
        self.assertEqual(project.properties, b"1234")

    def test_file_growth_during_open_is_rejected(self) -> None:
        self.path.write_bytes(b"Dim x\n")
        original_open = Path.open

        def growing_open(path, *args, **kwargs):
            if path == self.path:
                with original_open(path, "ab") as growing:
                    growing.write(b"x" * 64)
            return original_open(path, *args, **kwargs)

        with patch("lc_packer.project.MAX_EXPANDED_BYTES", 16):
            with patch.object(Path, "open", growing_open):
                with self.assertRaisesRegex(ProjectError, "变化|大小|限制"):
                    read_project(self.path)

    def test_symlinked_source_and_resources_cannot_embed_outside_files(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / "resource.bin"
        target.write_bytes(b"outside fixture")
        source_link = self.root / "linked.mq"
        try:
            source_link.symlink_to(target)
        except OSError:
            self.skipTest("Creating symlinks requires permission on this Windows installation")
        with self.assertRaisesRegex(ProjectError, "普通文件|链接"):
            read_project(source_link)
        self.path.write_bytes(b"Dim fixture\n")
        for suffix in (".prop", ".uip", ".atc"):
            with self.subTest(suffix=suffix):
                resource_link = self.path.with_suffix(suffix)
                resource_link.symlink_to(target)
                try:
                    with self.assertRaisesRegex(ProjectError, "普通文件|链接"):
                        read_project(self.path)
                finally:
                    resource_link.unlink()

    def test_malformed_mqb_explains_archive_error_in_chinese(self) -> None:
        archive = self.root / "broken.mqb"
        archive.write_bytes(b"Dim this_is_plain_source\n")
        with self.assertRaisesRegex(ProjectError, "MQB.*ZIP.*归档.*[.]mq"):
            read_project(archive)


if __name__ == "__main__":
    unittest.main()
