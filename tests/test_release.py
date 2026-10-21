"""The complete release ZIP contains only explicitly selected project files."""
import hashlib
import json
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

    def binary_entry(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {'path': path, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def test_v2_includes_only_verified_builtin_template_and_offline_packages(self):
        template = self.binary_entry('lc_packer/assets/templates/root-510.apk', b'clean-template')
        package = self.binary_entry('tool-packages/tool.jar', b'official-tool')
        source = self.binary_entry('tool-packages/source.tar.gz', b'corresponding-source')
        catalog = {'packages': [{'filename': 'tool.jar', **{k:v for k,v in package.items() if k != 'path'}}],
                   'source_archives': [{'filename': 'source.tar.gz', **{k:v for k,v in source.items() if k != 'path'}}]}
        catalog_path = self.root / 'lc_packer/data/tools_catalog.json'
        catalog_path.parent.mkdir()
        catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
        self.binary_entry('tool-packages/unused-private.zip', b'excluded')
        self.manifest.update(assets=[template], tool_packages=True)
        self.manifest['files'].append('lc_packer/data/tools_catalog.json')
        create_archive(self.root, self.manifest, self.exe, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {'LC打包.exe', *self.manifest['files'], template['path'], package['path'], source['path']})
            self.assertEqual(archive.read(template['path']), b'clean-template')

    def test_v2_rejects_changed_template_before_creating_archive(self):
        asset = self.binary_entry('lc_packer/assets/templates/root-510.apk', b'expected')
        self.manifest['assets'] = [asset]
        (self.root / asset['path']).write_bytes(b'tampered')
        with self.assertRaises(ValueError): create_archive(self.root, self.manifest, self.exe, self.output)
        self.assertFalse(self.output.exists())

    def test_v2_assets_cannot_whitelist_private_apks(self):
        asset = self.binary_entry('private.apk', b'private-project')
        self.manifest['assets'] = [asset]
        with self.assertRaises(ValueError): create_archive(self.root, self.manifest, self.exe, self.output)
        self.assertFalse(self.output.exists())

    def test_v2_packages_cannot_include_private_keys_or_escape(self):
        self.manifest['tool_packages'] = True
        path = self.root / 'lc_packer/data/tools_catalog.json'
        path.parent.mkdir()
        for name in ('private.p12', '../private.zip', 'C:/private.zip', 'folder\\private.zip'):
            path.write_text(json.dumps({'packages': [{'filename': name, 'size': 0, 'sha256': '0'*64}]}), encoding='utf-8')
            with self.subTest(name=name), self.assertRaises(ValueError):
                create_archive(self.root, self.manifest, self.exe, self.output)
            self.assertFalse(self.output.exists())

    def test_v2_includes_each_template_pinned_in_the_builtin_catalog(self):
        assets = [self.binary_entry('lc_packer/assets/templates/root-510.apk', b'root-runtime'),
                  self.binary_entry('lc_packer/assets/templates/lite.apk', b'lite-runtime')]
        catalog = self.root / 'lc_packer/data/templates_catalog.json'
        catalog.parent.mkdir()
        catalog.write_text(json.dumps({'templates': [
            {'file': Path(entry['path']).name, 'sha256': entry['sha256'], 'size': entry['size']}
            for entry in assets]}), encoding='utf-8')
        self.manifest['assets'] = assets
        create_archive(self.root, self.manifest, self.exe, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read(assets[1]['path']), b'lite-runtime')

    def test_v2_template_manifest_cannot_override_catalog_pin(self):
        asset = self.binary_entry('lc_packer/assets/templates/root-510.apk', b'wrong-runtime')
        catalog = self.root / 'lc_packer/data/templates_catalog.json'
        catalog.parent.mkdir()
        catalog.write_text(json.dumps({'templates': [{'file': 'root-510.apk', 'sha256': '0'*64,
                                                       'size': asset['size']}]}), encoding='utf-8')
        self.manifest['assets'] = [asset]
        with self.assertRaises(ValueError): create_archive(self.root, self.manifest, self.exe, self.output)
        self.assertFalse(self.output.exists())
