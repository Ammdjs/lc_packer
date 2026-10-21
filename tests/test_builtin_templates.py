"""The built-in catalog is data, never a source of arbitrary filesystem paths."""
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lc_packer.templates import TemplateError


class BuiltinTemplatesTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('lc_packer.builtin_templates')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / 'templates'
        self.assets.mkdir()
        self.catalog = self.root / 'catalog.json'
        self.payload = b'fake-template-resource'
        (self.assets / 'sample.apk').write_bytes(self.payload)
        self.row = {'id': 'sample', 'label': '示例模板', 'file': 'sample.apk',
                    'sha256': hashlib.sha256(self.payload).hexdigest(), 'size': len(self.payload),
                    'description': '未实测', 'verified': False, 'abis': ['x86'], 'format': 'old-0a14'}
        self.write_catalog([self.row])
        self.addCleanup(patch.stopall)
        patch.object(self.module, '_CATALOG_PATH', self.catalog).start()
        patch.object(self.module, '_TEMPLATES_DIR', self.assets).start()

    def write_catalog(self, rows):
        self.catalog.write_text(json.dumps({'templates': rows}), encoding='utf-8')

    def test_loads_metadata_and_resolves_a_real_file(self):
        self.assertEqual(self.module.load_templates(), (self.row,))
        self.assertEqual(self.module.template_path('sample'), self.assets / 'sample.apk')

    def test_registry_rejects_paths_and_invalid_hashes(self):
        for field, value in (('file', '../outside.apk'), ('file', 'C:\\outside.apk'),
                             ('file', '/outside.apk'), ('file', 'nested/sample.apk'),
                             ('file', '..\\outside.apk'), ('sha256', 'a' * 63),
                             ('sha256', 'g' * 64), ('id', '../sample'),
                             ('size', True), ('verified', 'false'), ('abis', ['../x86'])):
            with self.subTest(field=field, value=value):
                self.write_catalog([{**self.row, field: value}])
                with self.assertRaises(TemplateError):
                    self.module.load_templates()

    def test_registry_rejects_duplicate_ids_and_files(self):
        for second in ({**self.row, 'file': 'other.apk'}, {**self.row, 'id': 'other'}):
            with self.subTest(second=second):
                self.write_catalog([self.row, second])
                with self.assertRaises(TemplateError):
                    self.module.load_templates()

    def test_unknown_missing_and_modified_templates_are_rejected(self):
        with self.assertRaises(TemplateError):
            self.module.template_path('unknown')
        (self.assets / 'sample.apk').unlink()
        with self.assertRaises(TemplateError):
            self.module.template_path('sample')
        (self.assets / 'sample.apk').write_bytes(b'changed-content')
        with self.assertRaises(TemplateError):
            self.module.template_path('sample')
        (self.assets / 'sample.apk').write_bytes(b'x' * len(self.payload))
        with self.assertRaises(TemplateError):
            self.module.template_path('sample')

    def test_symbolic_link_to_an_outside_file_is_rejected(self):
        outside = self.root / 'outside.apk'
        outside.write_bytes(self.payload)
        link = self.assets / 'sample.apk'
        link.unlink()
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Creating file symlinks requires permission on this Windows installation')
        with self.assertRaises(TemplateError):
            self.module.template_path('sample')

    def test_malformed_catalog_is_reported_as_a_template_error(self):
        for content in ('{', '{"templates": {}}', '{"templates": [null]}', '{"templates": []}'):
            with self.subTest(content=content):
                self.catalog.write_text(content, encoding='utf-8')
                with self.assertRaises(TemplateError):
                    self.module.load_templates()


class PackagedBuiltinTemplatesTests(unittest.TestCase):
    def test_all_packaged_templates_have_matching_hashes_and_unique_names(self):
        module = importlib.import_module('lc_packer.builtin_templates')
        rows = module.load_templates()
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0]['id'], 'root-510')
        self.assertEqual([row['id'] for row in rows if row['verified']], ['root-510'])
        for row in rows:
            with self.subTest(template=row['id']):
                path = module.template_path(row['id'])
                self.assertEqual(path.name, row['file'])
                self.assertEqual(path.stat().st_size, row['size'])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row['sha256'])
                self.assertEqual(row['format'], 'old-0a14')
