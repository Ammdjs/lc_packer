"""The complete release ZIP contains only explicitly selected project files."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from lc_packer.release import create_archive


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "lc_packer").mkdir()
        (self.root / "lc_packer/__init__.py").write_text('"""LC packer."""', encoding="utf-8")
        (self.root / "README.md").write_text("LC打包", encoding="utf-8")
        self.exe = self.root / "LC打包.exe"
        self.exe.write_bytes(b"release-executable")
        self.output = self.root / "release.zip"
        self.manifest = {"version": "1.0.0", "files": ["README.md", "lc_packer/__init__.py"]}

    def test_archive_has_root_exe_and_only_manifest_project_files(self):
        for name in (".git/config", ".superpowers/notes.md", "local-config.json", "artifacts/private.p12", "lc_packer/__pycache__/cache.pyc"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"must-stay-local")
        create_archive(self.root, self.manifest, self.exe, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {"LC打包.exe", "README.md", "lc_packer/__init__.py"})
            self.assertEqual(archive.read("LC打包.exe"), self.exe.read_bytes())
            self.assertIsNone(archive.testzip())

    def test_manifest_cannot_include_internal_or_git_files(self):
        for name in (".git/config", ".gitignore", ".superpowers/note.md", "artifacts/note.txt", "local-config.json", "private.credentials.json", "signing.p12"):
            with self.subTest(name=name):
                manifest = {"version": "1.0.0", "files": [name]}
                with self.assertRaises(ValueError):
                    create_archive(self.root, manifest, self.exe, self.output)
                self.assertFalse(self.output.exists())

    def test_manifest_cannot_escape_project_directory(self):
        for name in ("../outside.txt", "/outside.txt", "C:/outside.txt", "folder\\outside.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                create_archive(self.root, {"files": [name]}, self.exe, self.output)

    def test_existing_archive_is_preserved(self):
        self.output.write_bytes(b"existing-release")
        with self.assertRaises(FileExistsError):
            create_archive(self.root, self.manifest, self.exe, self.output)
        self.assertEqual(self.output.read_bytes(), b"existing-release")

    def test_duplicate_windows_names_are_rejected(self):
        manifest = {"files": ["README.md", "readme.md"]}
        with self.assertRaises(ValueError):
            create_archive(self.root, manifest, self.exe, self.output)

    def test_missing_project_file_does_not_leave_partial_archive(self):
        self.manifest["files"].append("missing.py")
        with self.assertRaises(FileNotFoundError):
            create_archive(self.root, self.manifest, self.exe, self.output)
        self.assertFalse(self.output.exists())
