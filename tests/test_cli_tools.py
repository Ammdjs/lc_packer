import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lc_packer.cli import main
from lc_packer.toolchain import ToolPackage


class ToolCliTests(unittest.TestCase):
    def test_install_and_status_use_real_offline_service_and_requested_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_dir = root / 'tool-packages'
            package_dir.mkdir()
            data = b'portable-tool-fixture'
            (package_dir / 'fixture.jar').write_bytes(data)
            package = ToolPackage('fixture', '测试工具', '1', 'fixture.jar', hashlib.sha256(data).hexdigest(),
                                  len(data), 'jar', 'fixture', '', ('tool.jar',), {'apktool': 'tool.jar'},
                                  'https://example.com/tool.jar', 'https://example.com/license')
            output = io.StringIO()
            with patch('lc_packer.cli.load_catalog', return_value=(package,)), contextlib.redirect_stdout(output):
                self.assertEqual(main(['install-tools', '--root', str(root)]), 0)
                self.assertEqual(main(['tools-status', '--root', str(root)]), 0)
            self.assertEqual((root / 'tools/fixture/tool.jar').read_bytes(), data)
            self.assertIn('installed', output.getvalue())
            self.assertFalse((root / 'local-config.json').exists())

    def test_failed_tool_install_returns_nonzero_and_actionable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = ToolPackage('fixture', '测试工具', '1', 'missing.jar', '0'*64, 10,
                                  'jar', 'fixture', '', ('tool.jar',), {'apktool': 'tool.jar'},
                                  'https://example.com/tool.jar', 'https://example.com/license')
            errors = io.StringIO()
            with patch('lc_packer.cli.load_catalog', return_value=(package,)), contextlib.redirect_stderr(errors):
                self.assertEqual(main(['install-tools', '--root', temporary]), 2)
            self.assertIn('missing.jar', errors.getvalue())
