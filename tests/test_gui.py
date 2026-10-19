"""Offscreen Qt tests for user actions and asynchronous project binding."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QScrollArea, QLabel
from PySide6.QtGui import QFontMetrics
from lc_packer.app import default_settings
from lc_packer.diagnostics import CheckResult, EnvironmentReport
from lc_packer.gui import PackerWindow
from lc_packer.compiler import CaptureCancelled


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = PackerWindow(default_settings(), Path(self.temp.name) / "settings.json")
        self.addCleanup(self.window.close)

    def wait_done(self):
        deadline = time.monotonic() + 3
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertFalse(self.window.busy, "Qt worker did not finish")

    def test_real_qt_designer_form_contains_every_setting(self):
        self.assertIsInstance(self.window, QMainWindow)
        self.assertEqual(self.window.pages.count(), 5)
        self.assertEqual(set(self.window.edits), set(default_settings()))
        self.assertFalse(self.window.isVisible())

    def test_chinese_text_has_a_real_font_in_offscreen_render(self):
        self.assertTrue(QFontMetrics(self.window.font()).inFontUcs4(ord("测")))

    @unittest.skipUnless(os.name == "nt", "Windows path separator normalization")
    def test_template_inspection_accepts_qfiledialog_forward_slashes(self):
        self.window.edits["template"].setText("C:/templates/base.apk")
        self.window.handle_result("template", ("C:\\templates\\base.apk", SimpleNamespace(abis=("x86",))))
        self.assertEqual(self.window.required(QLabel, "templateSummary").text(), "x86")

    def test_small_window_keeps_form_fields_accessible_with_scrolling(self):
        self.window.resize(960, 660)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.size().toTuple(), (960, 660))
        areas = self.window.findChildren(QScrollArea)
        self.assertEqual(len(areas), 3)
        package_scroll = self.window.required(QScrollArea, "packagePageScroll")
        self.assertGreater(package_scroll.verticalScrollBar().maximum(), 0)

    def test_direct_source_edit_invalidates_lc(self):
        self.window.edits["compiled"].setText("old.lc")
        self.window.edits["source"].setText("another.mqb")
        self.assertEqual(self.window.edits["compiled"].text(), "")

    def test_late_capture_result_is_discarded_for_changed_project(self):
        revision = self.window.source_revision
        self.window.edits["source"].setText("other.mqb")
        self.window.handle_result("capture", (revision, SimpleNamespace(path=Path("old.lc"), sha256="hash")))
        self.assertEqual(self.window.edits["compiled"].text(), "")

    def test_build_button_calls_shared_service_with_snapshot_and_secret(self):
        output = SimpleNamespace(path=Path("ready.apk"), compilation_mode="precompiled", warnings=())
        self.window.password.setText("private-secret")
        with patch("lc_packer.gui.build_from_settings", return_value=output) as build:
            self.window.build_button.click()
            self.wait_done()
        args, kwargs = build.call_args
        self.assertEqual(args[1], "private-secret")
        self.assertNotIn("private-secret", self.window.logs.toPlainText())
        self.assertIn("ready.apk", self.window.logs.toPlainText())

    def test_diagnostic_table_details_and_export_preserve_error(self):
        report = EnvironmentReport((CheckResult("java", "error", "Java", "exit 7: missing runtime", "选择 JDK 17", True, "C:/missing/java.exe"),))
        with patch("lc_packer.gui.check_environment", return_value=report):
            self.window.doctor()
            self.wait_done()
        self.assertEqual(self.window.checks.rowCount(), 1)
        self.window.checks.selectRow(0)
        self.assertIn("exit 7", self.window.details.toPlainText())
        target = Path(self.temp.name) / "report.json"
        self.window.write_report(target)
        self.assertIn("missing runtime", target.read_text(encoding="utf-8"))

    def test_capture_button_uses_shared_service_and_binds_result(self):
        result = SimpleNamespace(path=Path("new.lc"), sha256="fingerprint")
        with patch("lc_packer.gui.capture_from_settings", return_value=result) as capture:
            self.window.capture_button.click()
            self.wait_done()
        self.assertEqual(self.window.edits["compiled"].text(), "new.lc")
        self.assertIn("cancel_event", capture.call_args.kwargs)

    def test_save_config_excludes_passwords(self):
        self.window.password.setText("secret-store")
        self.window.key_password.setText("secret-key")
        self.window.save()
        self.assertNotIn("secret", self.window.config_path.read_text(encoding="utf-8"))

    def test_worker_redacts_original_password_even_after_field_changes(self):
        release = threading.Event()
        def fail(*args, **kwargs):
            release.wait(1)
            raise ValueError("failure private-old-password")
        self.window.password.setText("private-old-password")
        with patch("lc_packer.gui.build_from_settings", side_effect=fail):
            self.window.build_button.click()
            self.window.password.setText("different-password")
            release.set()
            self.wait_done()
        self.assertNotIn("private-old-password", self.window.logs.toPlainText())
        self.assertIn("REDACTED", self.window.logs.toPlainText())

    def test_close_requests_capture_cancel_and_keeps_thread_alive_until_done(self):
        def capture(*args, **kwargs):
            kwargs["cancel_event"].wait(1)
            raise CaptureCancelled("cancelled")
        with patch("lc_packer.gui.capture_from_settings", side_effect=capture):
            self.window.capture_button.click()
            self.assertFalse(self.window.close())
            self.assertTrue(self.window.cancel.is_set())
            self.wait_done()
        self.assertTrue(self.window.close())

    def test_new_key_button_calls_shared_creation_service(self):
        target = str(Path(self.temp.name) / "new.p12")
        self.window.password.setText("new-key-password")
        with patch("lc_packer.gui.QFileDialog.getSaveFileName", return_value=(target, "")), patch("lc_packer.gui.create_keystore", return_value=Path(target)) as create:
            self.window.required(QPushButton, "newKeyButton").click()
            self.wait_done()
        self.assertEqual(create.call_args.args[0]["keystore"], target)
        self.assertEqual(create.call_args.args[1], "new-key-password")

    def test_report_is_marked_stale_after_configuration_edit(self):
        report = EnvironmentReport((CheckResult("java", "pass", "Java", "ok", "", True),))
        self.window.handle_result("report", (self.window.settings_revision, report))
        self.window.edits["java"].setText("changed.exe")
        target = Path(self.temp.name) / "stale.json"
        self.window.write_report(target)
        import json
        self.assertFalse(json.loads(target.read_text(encoding="utf-8"))["configuration_current"])
        self.assertIn("配置已变化", self.window.summary_label.text())
