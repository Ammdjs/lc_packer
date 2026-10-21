"""Offline installation checks using real, disposable ZIP/JAR packages."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import warnings
import zipfile

from lc_packer.toolchain import (
    ToolInstallError,
    ToolPackage,
    install_packages,
    installed_settings,
    load_catalog,
    package_status,
)
import lc_packer.toolchain as toolchain


class ToolchainTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "程序 中文"
        self.cache = self.root / "tool-packages"
        self.cache.mkdir(parents=True)

    def zip_package(self, members=None, **changes) -> ToolPackage:
        path = self.cache / "jdk.zip"
        if members is None:
            members = [("jdk/bin/java.exe", b"java"), ("jdk/bin/keytool.exe", b"keytool"),
                       ("jdk/LICENSE", b"license")]
        with warnings.catch_warnings():
            # Deliberate duplicate-member fixtures should not pollute test output.
            warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, contents in members:
                    archive.writestr(name, contents)
        package = ToolPackage(
            id="jdk17", label="JDK 17", version="17-test", filename="jdk.zip",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size,
            kind="zip", target="jdk17", prefix="jdk/",
            required=("bin/java.exe", "bin/keytool.exe"),
            settings={"java": "bin/java.exe", "keytool": "bin/keytool.exe"},
            source_url="https://example.org/jdk.zip", license_url="https://example.org/license",
        )
        return replace(package, **changes)

    def jar_package(self, content=b"real jar bytes") -> ToolPackage:
        path = self.cache / "apktool.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/MANIFEST.MF", content)
        return ToolPackage(
            id="apktool", label="Apktool", version="3-test", filename="apktool.jar",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size,
            kind="jar", target="apktool", prefix="", required=("apktool.jar",),
            settings={"apktool": "apktool.jar"}, source_url="https://example.org/apktool.jar",
            license_url="https://example.org/license",
        )

    def assert_no_stage(self) -> None:
        tools = self.root / "tools"
        if tools.exists():
            self.assertFalse([p for p in tools.iterdir()
                              if p.name.startswith((".lc-stage-", ".lc-source-"))])

    def test_zip_and_jar_install_return_actual_paths_and_keep_licenses(self) -> None:
        jdk, apktool = self.zip_package(), self.jar_package()
        jar_bytes = (self.cache / "apktool.jar").read_bytes()
        result = install_packages(self.root, packages=(jdk, apktool))
        self.assertEqual(result, {
            "java": str(self.root / "tools/jdk17/bin/java.exe"),
            "keytool": str(self.root / "tools/jdk17/bin/keytool.exe"),
            "apktool": str(self.root / "tools/apktool/apktool.jar"),
        })
        self.assertEqual((self.root / "tools/jdk17/LICENSE").read_bytes(), b"license")
        self.assertEqual((self.root / "tools/apktool/apktool.jar").read_bytes(), jar_bytes)
        self.assertEqual(package_status(self.root, jdk).state, "installed")
        self.assertEqual(installed_settings(self.root, (jdk, apktool)), result)
        self.assert_no_stage()

    def test_install_is_idempotent_and_works_without_original_package(self) -> None:
        package = self.zip_package()
        first = install_packages(self.root, packages=(package,))
        executable = Path(first["java"])
        before = executable.stat().st_mtime_ns
        (self.cache / package.filename).unlink()
        self.assertEqual(install_packages(self.root, packages=(package,)), first)
        self.assertEqual(executable.stat().st_mtime_ns, before)
        self.assertEqual(package_status(self.root, package).state, "installed")

    def test_missing_package_status_is_actionable(self) -> None:
        package = self.zip_package()
        self.assertEqual(package_status(self.root, package).state, "missing")
        (self.cache / package.filename).unlink()
        status = package_status(self.root, package)
        self.assertEqual(status.state, "package_missing")
        self.assertIn(package.filename, status.detail)
        with self.assertRaisesRegex(ToolInstallError, "安装包|离线包"):
            install_packages(self.root, packages=(package,))
        self.assertFalse((self.root / "tools").exists())

    def test_tampered_digest_and_size_do_not_create_tools_directory(self) -> None:
        package = self.zip_package()
        for damaged in (replace(package, sha256="0" * 64), replace(package, size=package.size + 1)):
            with self.subTest(damaged=damaged):
                with self.assertRaisesRegex(ToolInstallError, "校验|大小|完整"):
                    install_packages(self.root, packages=(damaged,))
                self.assertFalse((self.root / "tools").exists())

    def test_unmanaged_existing_target_is_never_overwritten(self) -> None:
        package = self.zip_package()
        target = self.root / "tools/jdk17"
        target.mkdir(parents=True)
        original = target / "personal.txt"
        original.write_bytes(b"keep me")
        self.assertEqual(package_status(self.root, package).state, "error")
        with self.assertRaisesRegex(ToolInstallError, "现有|管理|覆盖"):
            install_packages(self.root, packages=(package,))
        self.assertEqual(original.read_bytes(), b"keep me")
        self.assertEqual(list(target.iterdir()), [original])
        self.assert_no_stage()

    def test_managed_missing_or_changed_required_file_is_broken_and_repaired(self) -> None:
        package = self.zip_package()
        result = install_packages(self.root, packages=(package,))
        target = self.root / "tools/jdk17"
        personal = target / "my-notes.txt"
        personal.write_bytes(b"keep me")
        for content in (None, b"tampered"):
            with self.subTest(content=content):
                executable = Path(result["java"])
                if content is None:
                    executable.unlink()
                else:
                    executable.write_bytes(content)
                self.assertEqual(package_status(self.root, package).state, "broken")
                self.assertEqual(installed_settings(self.root, (package,)), {})
                install_packages(self.root, packages=(package,))
                self.assertEqual(executable.read_bytes(), b"java")
                self.assertEqual(personal.read_bytes(), b"keep me")
                self.assert_no_stage()

    def test_managed_repair_preserves_unknown_empty_directories(self) -> None:
        package = self.zip_package()
        result = install_packages(self.root, packages=(package,))
        personal = self.root / "tools/jdk17/my-empty-folder"
        personal.mkdir()
        Path(result["java"]).unlink()
        install_packages(self.root, packages=(package,))
        self.assertTrue(personal.is_dir())

    def test_missing_or_changed_nonrequired_file_is_broken_and_repaired(self) -> None:
        package = self.zip_package()
        install_packages(self.root, packages=(package,))
        license_file = self.root / "tools/jdk17/LICENSE"
        for contents in (None, b"tamper!", b"longer damaged license"):
            with self.subTest(contents=contents):
                if contents is None:
                    license_file.unlink()
                else:
                    license_file.write_bytes(contents)
                self.assertEqual(package_status(self.root, package).state, "broken")
                self.assertEqual(installed_settings(self.root, (package,)), {})
                install_packages(self.root, packages=(package,))
                self.assertEqual(license_file.read_bytes(), b"license")
                self.assertEqual(package_status(self.root, package).state, "installed")
                self.assert_no_stage()

    def test_extra_user_files_do_not_affect_managed_health(self) -> None:
        package = self.zip_package()
        installed = install_packages(self.root, packages=(package,))
        personal = self.root / "tools/jdk17/personal.txt"
        personal.write_bytes(b"user-owned")
        (personal.parent / "empty-user-folder").mkdir()
        self.assertEqual(package_status(self.root, package).state, "installed")
        personal.write_bytes(b"updated user notes")
        self.assertEqual(installed_settings(self.root, (package,)), installed)

    def test_legacy_marker_without_full_manifest_is_repaired(self) -> None:
        package = self.zip_package()
        install_packages(self.root, packages=(package,))
        marker_path = self.root / "tools/jdk17/.lc-tool.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker.pop("file_records", None)
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        self.assertEqual(package_status(self.root, package).state, "broken")
        install_packages(self.root, packages=(package,))
        self.assertEqual(package_status(self.root, package).state, "installed")

    def test_repair_keeps_complete_backup_including_last_user_writes(self) -> None:
        package = self.zip_package()
        installed = install_packages(self.root, packages=(package,))
        target = self.root / "tools/jdk17"
        Path(installed["java"]).write_bytes(b"old-damaged")
        personal = target / "personal.txt"
        personal.write_bytes(b"snapshot notes")
        real_commit = toolchain._commit

        def concurrent_write(stage, destination, package, tools):
            (destination / "late-user-file.txt").write_bytes(b"created during repair")
            (destination / "personal.txt").write_bytes(b"latest notes")
            return real_commit(stage, destination, package, tools)

        messages = []
        with patch.object(toolchain, "_commit", concurrent_write):
            install_packages(self.root, packages=(package,), log=messages.append)
        backups = list((self.root / "tools").glob(".lc-backup-*"))
        self.assertEqual(len(backups), 1)
        backup = backups[0]
        self.assertEqual((backup / "late-user-file.txt").read_bytes(), b"created during repair")
        self.assertEqual((backup / "personal.txt").read_bytes(), b"latest notes")
        self.assertEqual((backup / "bin/java.exe").read_bytes(), b"old-damaged")
        self.assertEqual(personal.read_bytes(), b"snapshot notes")
        self.assertEqual(Path(installed["java"]).read_bytes(), b"java")
        self.assertTrue(any(str(backup) in message for message in messages))
        self.assert_no_stage()

    def test_jar_source_change_after_verification_cannot_change_installed_bytes(self) -> None:
        package = self.jar_package()
        archive = self.cache / package.filename
        original = archive.read_bytes()
        tampered = b"!" * len(original)

        def mutate_source(message):
            if "正在复制" in message:
                archive.write_bytes(tampered)

        install_packages(self.root, packages=(package,), log=mutate_source)
        installed = self.root / "tools/apktool/apktool.jar"
        self.assertEqual(archive.read_bytes(), tampered)
        self.assertEqual(installed.read_bytes(), original)
        self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), package.sha256)
        self.assertEqual(package_status(self.root, package).state, "installed")
        self.assert_no_stage()

    def test_source_change_before_verified_snapshot_is_rejected(self) -> None:
        package = self.jar_package()
        archive = self.cache / package.filename
        real_hash = toolchain._hash_stream
        changed = False

        def mutate_after_hash(stream, cancel_event=None):
            nonlocal changed
            digest = real_hash(stream, cancel_event)
            if not changed:
                changed = True
                archive.write_bytes(b"!" * package.size)
            return digest

        with patch.object(toolchain, "_hash_stream", mutate_after_hash):
            with self.assertRaisesRegex(ToolInstallError, "SHA256|校验"):
                install_packages(self.root, packages=(package,))
        self.assertFalse((self.root / "tools/apktool").exists())
        self.assert_no_stage()

    def test_repair_refuses_new_package_collision_with_user_file(self) -> None:
        package = self.zip_package()
        installed = install_packages(self.root, packages=(package,))
        personal = self.root / "tools/jdk17/personal.txt"
        personal.write_bytes(b"user-owned")
        updated = self.zip_package([
            ("jdk/bin/java.exe", b"new-java"), ("jdk/bin/keytool.exe", b"keytool"),
            ("jdk/personal.txt", b"replacement")], version="new-version")
        with self.assertRaisesRegex(ToolInstallError, "用户文件|冲突"):
            install_packages(self.root, packages=(updated,))
        self.assertEqual(personal.read_bytes(), b"user-owned")
        self.assertEqual(Path(installed["java"]).read_bytes(), b"java")
        self.assert_no_stage()

    def test_failed_repair_rename_rolls_back_existing_package(self) -> None:
        package = self.zip_package()
        installed = install_packages(self.root, packages=(package,))
        Path(installed["java"]).write_bytes(b"old-damaged")
        personal = self.root / "tools/jdk17/personal.txt"
        personal.write_bytes(b"keep")
        real_rename = Path.rename

        def fail_publish(path, destination):
            if path.name.startswith(".lc-stage-"):
                raise PermissionError("模拟文件被其他程序占用")
            return real_rename(path, destination)

        with patch.object(Path, "rename", fail_publish):
            with self.assertRaisesRegex(ToolInstallError, "权限|安装失败"):
                install_packages(self.root, packages=(package,))
        self.assertEqual(Path(installed["java"]).read_bytes(), b"old-damaged")
        self.assertEqual(personal.read_bytes(), b"keep")
        self.assertFalse(list((self.root / "tools").glob(".lc-backup-*")))
        self.assert_no_stage()

    def test_forged_or_invalid_marker_does_not_authorize_removal(self) -> None:
        package = self.zip_package()
        target = self.root / "tools/jdk17"
        target.mkdir(parents=True)
        marker = target / ".lc-tool.json"
        marker.write_text('{"id":"jdk17"}', encoding="utf-8")
        original = target / "personal.txt"
        original.write_bytes(b"keep")
        with self.assertRaises(ToolInstallError):
            install_packages(self.root, packages=(package,))
        self.assertEqual(original.read_bytes(), b"keep")
        self.assertEqual(marker.read_text(encoding="utf-8"), '{"id":"jdk17"}')

    def test_cancel_before_install_does_not_create_tools_directory(self) -> None:
        package = self.zip_package()
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(ToolInstallError, "取消"):
            install_packages(self.root, packages=(package,), cancel_event=event)
        self.assertFalse((self.root / "tools").exists())

    def test_cancel_during_streaming_cleans_stage(self) -> None:
        # A stored archive avoids triggering the independent compression-bomb limit.
        package = self.zip_package([("jdk/bin/java.exe", b"x" * (3 * 1024 * 1024)),
                                    ("jdk/bin/keytool.exe", b"key")])
        path = self.cache / package.filename
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("jdk/bin/java.exe", b"x" * (3 * 1024 * 1024))
            archive.writestr("jdk/bin/keytool.exe", b"key")
        package = replace(package, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size)
        root = self.root

        class CancelAfterWrite:
            def is_set(self):
                return any(p.stat().st_size >= 1024 * 1024
                           for p in (root / "tools").glob(".lc-stage-*/bin/java.exe"))

        with self.assertRaisesRegex(ToolInstallError, "取消"):
            install_packages(self.root, packages=(package,), cancel_event=CancelAfterWrite())
        self.assertFalse((self.root / "tools/jdk17").exists())
        self.assert_no_stage()

    def test_cancel_during_verified_snapshot_cleans_private_source(self) -> None:
        package = self.jar_package(content=b"x" * (3 * 1024 * 1024))
        event = threading.Event()
        real_temporary_file = tempfile.TemporaryFile

        @contextmanager
        def cancel_after_snapshot_write(*args, **kwargs):
            with real_temporary_file(*args, **kwargs) as snapshot:
                class CancellingWriter:
                    def write(self, contents):
                        count = snapshot.write(contents)
                        event.set()
                        return count

                yield CancellingWriter()

        with patch.object(toolchain.tempfile, "TemporaryFile", cancel_after_snapshot_write):
            with self.assertRaisesRegex(ToolInstallError, "取消"):
                install_packages(self.root, packages=(package,), cancel_event=event)
        self.assertTrue(event.is_set())
        self.assertFalse((self.root / "tools/apktool").exists())
        self.assert_no_stage()

    def test_later_failure_keeps_previously_committed_package(self) -> None:
        jdk, jar = self.zip_package(), self.jar_package()
        with self.assertRaises(ToolInstallError):
            install_packages(self.root, packages=(jdk, replace(jar, sha256="0" * 64)))
        self.assertEqual(package_status(self.root, jdk).state, "installed")
        self.assertFalse((self.root / "tools/apktool").exists())
        self.assert_no_stage()

    def test_rejects_windows_unsafe_member_paths(self) -> None:
        unsafe = ("../escape", "jdk/../escape", "/escape", "C:/escape", "C:escape",
                  "\\\\server\\share\\escape", "jdk\\bin\\evil.exe", "jdk/./evil",
                  "jdk/bin/java.exe:payload", "jdk/NUL.txt", "jdk/aux/file", "jdk/COM1",
                  "jdk/trailing. ", "jdk/bin/evil\x00.exe", "jdk/evil?.exe")
        for name in unsafe:
            with self.subTest(name=name):
                package = self.zip_package([(name, b"evil")])
                with self.assertRaises(ToolInstallError):
                    install_packages(self.root, packages=(package,))
                self.assertFalse((self.root / "tools/jdk17").exists())
                self.assertFalse((self.root / "escape").exists())
                self.assert_no_stage()

    def test_rejects_case_alias_duplicates_and_file_directory_collisions(self) -> None:
        cases = (
            [("jdk/bin/java.exe", b"a"), ("jdk/BIN/JAVA.EXE", b"b")],
            [("jdk/bin/java.exe", b"a"), ("jdk/BIN/keytool.exe", b"b")],
            [("jdk/bin", b"file"), ("jdk/bin/java.exe", b"a")],
            [("jdk/bin/java.exe", b"a"), ("jdk/bin", b"file")],
            [("jdk/bin/", b""), ("jdk/bin/", b"")],
            [("jdk/.lc-tool.json", b"{}")] ,
        )
        for members in cases:
            with self.subTest(members=members):
                package = self.zip_package(members)
                with self.assertRaises(ToolInstallError):
                    install_packages(self.root, packages=(package,))
                self.assertFalse((self.root / "tools/jdk17").exists())
                self.assert_no_stage()

    def test_rejects_links_reparse_entries_and_special_files(self) -> None:
        for mode, low_attributes in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFIFO | 0o644, 0),
                                     (stat.S_IFREG | 0o644, 0x400)):
            with self.subTest(mode=mode, attrs=low_attributes):
                entry = zipfile.ZipInfo("jdk/bin/java.exe")
                entry.create_system = 3
                entry.external_attr = (mode << 16) | low_attributes
                package = self.zip_package([(entry, b"../outside")])
                with self.assertRaises(ToolInstallError):
                    install_packages(self.root, packages=(package,))
                self.assertFalse((self.root / "tools/jdk17").exists())
                self.assert_no_stage()

    def test_rejects_duplicate_stripped_prefix_directory_entries(self) -> None:
        package = self.zip_package([
            ("jdk/", b""), ("jdk/", b""),
            ("jdk/bin/java.exe", b"java"), ("jdk/bin/keytool.exe", b"key")])
        with self.assertRaisesRegex(ToolInstallError, "重复|冲突"):
            install_packages(self.root, packages=(package,))
        self.assertFalse((self.root / "tools/jdk17").exists())
        self.assert_no_stage()

    def test_rejects_encrypted_archive_before_extracting(self) -> None:
        package = self.zip_package()
        path = self.cache / package.filename
        raw = bytearray(path.read_bytes())
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            location = raw.index(signature)
            flags = struct.unpack_from("<H", raw, location + offset)[0]
            struct.pack_into("<H", raw, location + offset, flags | 1)
        path.write_bytes(raw)
        package = replace(package, sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ToolInstallError, "加密"):
            install_packages(self.root, packages=(package,))
        self.assertFalse((self.root / "tools/jdk17").exists())
        self.assert_no_stage()

    def test_rejects_compression_bomb(self) -> None:
        package = self.zip_package([("jdk/bin/java.exe", b"0" * (3 * 1024 * 1024)),
                                    ("jdk/bin/keytool.exe", b"key")])
        with self.assertRaisesRegex(ToolInstallError, "压缩|大小|限制"):
            install_packages(self.root, packages=(package,))
        self.assertFalse((self.root / "tools/jdk17").exists())
        self.assert_no_stage()

    def test_invalid_deflate_stream_has_actionable_error_and_cleans_stage(self) -> None:
        package = self.zip_package()
        path = self.cache / package.filename
        with zipfile.ZipFile(path) as archive:
            info = archive.infolist()[0]
        raw = bytearray(path.read_bytes())
        filename_size, extra_size = struct.unpack_from("<HH", raw, info.header_offset + 26)
        start = info.header_offset + 30 + filename_size + extra_size
        raw[start:start + info.compress_size] = b"\xff" * info.compress_size
        path.write_bytes(raw)
        package = replace(package, sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ToolInstallError, "安装包|安装失败|损坏"):
            install_packages(self.root, packages=(package,))
        self.assertFalse((self.root / "tools/jdk17").exists())
        self.assert_no_stage()

    def test_missing_required_file_never_commits(self) -> None:
        package = self.zip_package([("jdk/bin/java.exe", b"java")])
        with self.assertRaisesRegex(ToolInstallError, "缺少|必需"):
            install_packages(self.root, packages=(package,))
        self.assertFalse((self.root / "tools/jdk17").exists())
        self.assert_no_stage()

    def test_prefix_is_exact_and_empty_prefix_extracts_root(self) -> None:
        package = self.zip_package([("wrong/bin/java.exe", b"java")])
        with self.assertRaisesRegex(ToolInstallError, "前缀|目录"):
            install_packages(self.root, packages=(package,))
        package = self.zip_package([("bin/java.exe", b"java"), ("bin/keytool.exe", b"key")], prefix="")
        install_packages(self.root, packages=(package,))
        self.assertEqual((self.root / "tools/jdk17/bin/java.exe").read_bytes(), b"java")

    def test_package_paths_cannot_escape_root(self) -> None:
        package = self.zip_package()
        invalid = (replace(package, target="../outside"), replace(package, filename="../jdk.zip"),
                   replace(package, prefix="../jdk/"), replace(package, required=("../secret",)),
                   replace(package, settings={"java": "C:/outside.exe"}))
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ToolInstallError):
                    install_packages(self.root, packages=(candidate,))
                self.assertFalse((self.root / "tools").exists())

    def test_reparse_tools_directory_is_rejected_without_touching_destination(self) -> None:
        package = self.zip_package()
        outside = self.root.parent / "outside"
        outside.mkdir()
        tools = self.root / "tools"
        try:
            tools.symlink_to(outside, target_is_directory=True)
        except OSError:
            # Directory junctions need no symlink privilege on ordinary Windows.
            process = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(tools), str(outside)],
                                     capture_output=True, check=False)
            if process.returncode:
                self.skipTest("当前帐户无法创建符号链接或目录联接")
            self.addCleanup(tools.rmdir)
        else:
            self.addCleanup(tools.unlink)
        with self.assertRaises(ToolInstallError):
            install_packages(self.root, packages=(package,))
        self.assertEqual(list(outside.iterdir()), [])

    def test_catalog_reads_real_json_and_rejects_unsafe_entries(self) -> None:
        package = self.zip_package()
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps({"packages": [asdict(package)]}), encoding="utf-8")
        with patch("lc_packer.toolchain.CATALOG_PATH", catalog):
            self.assertEqual(load_catalog(), (package,))
            catalog.write_text(json.dumps({"packages": [asdict(replace(package, target="../escape"))]}), encoding="utf-8")
            with self.assertRaises(ToolInstallError):
                load_catalog()

    def test_catalog_allows_release_source_metadata(self) -> None:
        package = self.zip_package()
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps({"schema_version": 1, "packages": [asdict(package)],
                                       "source_archives": [], "upstream_archives": []}), encoding="utf-8")
        with patch("lc_packer.toolchain.CATALOG_PATH", catalog):
            self.assertEqual(load_catalog(), (package,))

    def test_catalog_rejects_ambiguous_targets_and_unrecognized_package_fields(self) -> None:
        package = self.zip_package()
        catalog = self.root / "catalog.json"
        for entries in ([asdict(package), asdict(replace(package, id="other"))],
                        [dict(asdict(package), download_script="unexpected")]):
            with self.subTest(entries=entries):
                catalog.write_text(json.dumps({"packages": entries}), encoding="utf-8")
                with patch("lc_packer.toolchain.CATALOG_PATH", catalog):
                    with self.assertRaises(ToolInstallError):
                        load_catalog()


if __name__ == "__main__":
    unittest.main()
