"""Portable frozen paths and the windowed executable entry point."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from lc_packer import __version__


class DesktopTests(unittest.TestCase):
    def test_frozen_settings_live_beside_executable(self):
        from lc_packer.runtime import application_root
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", "E:/Portable/LC打包.exe"):
            self.assertEqual(application_root(), Path("E:/Portable"))

    def test_source_settings_live_in_project(self):
        from lc_packer.runtime import application_root
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(application_root(), Path(__file__).resolve().parent.parent)

    def test_windowed_cli_has_safe_input_and_explicit_log(self):
        from lc_packer.desktop import main
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "result.log"
            with patch.object(sys, "stdin", None), patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
                code = main(["--log-file", str(report), "compile", "test.mqb"])
            self.assertEqual(code, 2)
            self.assertIn("compiler CLI/API", report.read_text(encoding="utf-8"))

    def test_self_test_loads_actual_form_without_showing_window(self):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from lc_packer.desktop import main
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "self-test.json"
            self.assertEqual(main(["--self-test", str(report)]), 0)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["pages"], 5)
            self.assertFalse(data["window_visible"])
            self.assertTrue(data["ui_exists"] and data["stylesheet_exists"])
            self.assertEqual(data['application_version'], __version__)
            self.assertEqual(data['catalog_packages'], 4)
            self.assertTrue(data['builtin_template_present'])
            self.assertTrue(data['tool_installer_ui'])
            self.assertEqual(data['builtin_template_count'], 16)

    def test_external_path_cleanup_keeps_user_tools(self):
        from lc_packer.runtime import external_tool_path
        root = str(Path("C:/Temp/_MEI123"))
        paths = os.pathsep.join([root, str(Path(root) / "PySide6"), "E:/MyTools", root + "-other"])
        self.assertEqual(external_tool_path(paths, Path(root)).split(os.pathsep), ["E:/MyTools", root + "-other"])
