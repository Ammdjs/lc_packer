"""A bundled template carries the runtime without a sample user's payload."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from lc_packer.template_bundle import create_clean_template
from lc_packer.templates import TemplateError


class TemplateBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'original.apk'
        self.output = self.root / 'clean.apk'
        self.entries = {'AndroidManifest.xml': b'manifest', 'classes.dex': b'unchanged-runtime',
                        'lib/x86/engine.so': b'unchanged-native', 'res/drawable/icon.png': b'icon',
                        'assets/script.lc': b'\x0a\x14private-sample-script',
                        'assets/script.atc': b'private-attachment', 'assets/script.prop': b'private-project-id',
                        'assets/script.info': b'private-version', 'assets/config.json': b'{"scriptUpdateUrl":"https://private.example"}',
                        'assets/supplierconfig.json': b'{}', 'META-INF/OLD.RSA': b'old-signature'}
        self.entries['assets/inject/config.txt'] = json.dumps({'ClientTimeStamp': 99, 'ServerTimeStamp': 88,
            'TaskId': 9, 'SaveDir': '/private-task', 'TaskScriptUrl': 'http://private.example/script',
            'ScriptConfig': '{"TaskId":9,"SaveDir":"/private-task"}', 'ScriptUip': 'private-ui'}).encode()
        with zipfile.ZipFile(self.source, 'w') as archive:
            for name, value in self.entries.items(): archive.writestr(name, value)

    def test_payload_metadata_and_update_address_are_cleared(self):
        create_clean_template(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read('assets/script.lc'), b'\x0a\x14\0\0')
            self.assertEqual(archive.read('assets/script.atc'), b'')
            self.assertEqual(archive.read('assets/script.prop'), b'{}')
            self.assertEqual(json.loads(archive.read('assets/script.info')), {'ScriptVersion': '0'})
            config = json.loads(archive.read('assets/config.json'))
            self.assertFalse(config['runScriptUpdate'])
            self.assertEqual(config['scriptUpdateUrl'], '')
            inject = json.loads(archive.read('assets/inject/config.txt'))
            self.assertEqual(inject, {'ClientTimeStamp': 0, 'ServerTimeStamp': 0, 'TaskId': 0,
                'SaveDir': '', 'TaskScriptUrl': '', 'ScriptConfig': '{"TaskId":0,"SaveDir":""}', 'ScriptUip': ''})
            self.assertNotIn('META-INF/OLD.RSA', archive.namelist())
            self.assertFalse(any(b'private-' in archive.read(name) for name in archive.namelist()))

    def test_engine_resources_manifest_and_native_libraries_are_preserved(self):
        original = self.source.read_bytes()
        create_clean_template(self.source, self.output)
        self.assertEqual(self.source.read_bytes(), original)
        with zipfile.ZipFile(self.output) as archive:
            for name in ('AndroidManifest.xml', 'classes.dex', 'lib/x86/engine.so', 'res/drawable/icon.png', 'assets/supplierconfig.json'):
                self.assertEqual(archive.read(name), self.entries[name])

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b'keep')
        with self.assertRaises(FileExistsError): create_clean_template(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'keep')

    def rewrite_source(self, entries):
        with zipfile.ZipFile(self.source, 'w') as archive:
            for name, value in entries.items():
                archive.writestr(name, value)

    def test_unknown_runtime_format_keeps_its_own_four_byte_marker(self):
        self.entries['assets/script.lc'] = b'eflcprivate-script'
        self.rewrite_source(self.entries)
        create_clean_template(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read('assets/script.lc'), b'eflc')
            self.assertEqual(archive.read('classes.dex'), b'unchanged-runtime')
            self.assertFalse(any(b'private-' in archive.read(name) for name in archive.namelist()))

    def test_absent_runtime_configuration_is_not_invented(self):
        del self.entries['assets/config.json']
        del self.entries['assets/inject/config.txt']
        self.rewrite_source(self.entries)
        create_clean_template(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertNotIn('assets/config.json', archive.namelist())
            self.assertNotIn('assets/inject/config.txt', archive.namelist())
            self.assertNotIn('assets/out_script.lc', archive.namelist())

    def test_runtime_settings_survive_sample_configuration_cleanup(self):
        self.entries['assets/config.json'] = json.dumps({
            'scriptUpdateUrl': 'https://private.example', 'bootAutoStart': True,
            'runMode': {'pauseEnabled': True}}).encode()
        inject = json.loads(self.entries['assets/inject/config.txt'])
        inject['runtimeMode'] = 'keep-mode'
        self.entries['assets/inject/config.txt'] = json.dumps(inject).encode()
        self.rewrite_source(self.entries)
        create_clean_template(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            config = json.loads(archive.read('assets/config.json'))
            self.assertTrue(config['bootAutoStart'])
            self.assertEqual(config['runMode'], {'pauseEnabled': True})
            self.assertEqual(config['scriptUpdateUrl'], '')
            self.assertEqual(json.loads(archive.read('assets/inject/config.txt'))['runtimeMode'], 'keep-mode')

    def test_incomplete_payload_header_is_rejected_without_output(self):
        self.entries['assets/script.lc'] = b'abc'
        self.rewrite_source(self.entries)
        with self.assertRaises(TemplateError):
            create_clean_template(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_only_signature_metadata_is_removed(self):
        for name in ('META-INF/MANIFEST.MF', 'META-INF/OLD.SF', 'META-INF/OLD.DSA', 'META-INF/OLD.EC'):
            self.entries[name] = b'old-signature'
        self.entries['META-INF/services/runtime'] = b'keep-service'
        self.entries['classes2.dex'] = b'unchanged-second-dex'
        self.rewrite_source(self.entries)
        create_clean_template(self.source, self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read('META-INF/services/runtime'), b'keep-service')
            self.assertEqual(archive.read('classes2.dex'), b'unchanged-second-dex')
            self.assertFalse(any(name in archive.namelist() for name in (
                'META-INF/MANIFEST.MF', 'META-INF/OLD.RSA', 'META-INF/OLD.SF',
                'META-INF/OLD.DSA', 'META-INF/OLD.EC')))
