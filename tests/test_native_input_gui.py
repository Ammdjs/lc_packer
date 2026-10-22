"""Native MQ selection and user-facing feedback, without visible windows."""
import contextlib
import io
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from lc_packer.app import FIELDS, build_from_settings, default_settings, save_settings
from lc_packer.cli import main
from lc_packer.gui import PackerWindow


class NativeInputGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.window = PackerWindow(default_settings(), self.root / "settings.json")
        self.addCleanup(self.window.close)

    def wait_done(self):
        deadline = time.monotonic() + 3
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertFalse(self.window.busy, "Qt worker did not finish")

    def test_source_picker_accepts_native_mq_and_clears_previous_lc(self):
        source = self.root / "测试脚本.MQ"
        source.write_bytes(b'ShowMessage "native"')
        self.window.edits["source"].setText("previous.mqb")
        self.window.edits["compiled"].setText("previous.lc")
        observed_filters = []

        def choose_native(parent, title, current, file_filter):
            project_filter = file_filter.split(";;")[0]
            observed_filters.append(project_filter)
            return str(source), project_filter

        with patch("lc_packer.gui.QFileDialog.getOpenFileName", side_effect=choose_native):
            self.window.required(QPushButton, "sourceBrowse").click()

        self.assertEqual(len(observed_filters), 1)
        patterns = observed_filters[0].split("(")[-1].rstrip(")").split()
        for extension in ("*.mq", "*.MQ", "*.mqb", "*.MQB"):
            self.assertIn(extension, patterns)
        self.assertEqual(Path(self.window.edits["source"].text()), source)
        self.assertEqual(self.window.edits["compiled"].text(), "")
        self.assertFalse(self.window.isVisible())

    def test_build_feedback_identifies_missing_source_in_chinese(self):
        settings = dict.fromkeys(FIELDS, "configured")
        settings["source"] = ""
        for key, value in settings.items():
            self.window.edits[key].setText(value)
        self.window.build_button.click()
        self.wait_done()
        feedback = self.window.logs.toPlainText()
        self.assertIn("缺少", feedback)
        self.assertIn("脚本", feedback)
        self.assertNotIn("source", feedback)
        self.assertFalse(self.window.isVisible())

    def test_project_help_explains_native_companions_before_capture(self):
        source_help = self.window.required(QLabel, "sourceLabel").text()
        source_hint = self.window.edits["source"].placeholderText()
        project_hint = self.window.required(QLabel, "previewNote").text()
        capture_hint = self.window.required(QLabel, "compileHint").text()
        self.assertIn("MQ", source_help)
        self.assertIn("脚本", source_help)
        self.assertIn(".mq", source_hint)
        for suffix in (".prop", ".uip", ".atc"):
            self.assertIn(suffix, project_hint)
        self.assertIn("同名", project_hint)
        self.assertIn("自动", project_hint)
        self.assertLess(capture_hint.index("选择"), capture_hint.index("捕获"))
        self.assertIn("LC", capture_hint)


class NativeInputFeedbackTests(unittest.TestCase):
    def test_missing_build_fields_have_human_labels(self):
        required_labels = {
            "source": "脚本", "template": "模板", "compiled": "编译",
            "output": "输出", "java": "程序", "apktool": "构建",
            "aapt": "资源", "zipalign": "对齐", "apksigner": "签名",
            "keystore": "密钥", "workspace": "工作目录",
        }
        for key, human_label in required_labels.items():
            with self.subTest(field=key):
                settings = dict.fromkeys(FIELDS, "configured")
                settings[key] = ""
                with self.assertRaises(ValueError) as raised:
                    build_from_settings(settings, "password")
                feedback = str(raised.exception)
                self.assertIn("缺少", feedback)
                self.assertIn(human_label, feedback)

    def test_cli_help_advertises_mq_and_compatible_mqb_inputs(self):
        for arguments in (["--help"], ["inspect", "--help"]):
            with self.subTest(arguments=arguments):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                    main(arguments)
                self.assertEqual(raised.exception.code, 0)
                help_text = output.getvalue()
                self.assertIn(".mq", help_text)
                self.assertIn("MQB", help_text)
                self.assertIn("脚本", help_text)

    def test_cli_missing_project_uses_chinese_field_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "settings.json"
            settings = dict.fromkeys(FIELDS, "configured")
            settings["source"] = ""
            save_settings(config, settings)
            error = io.StringIO()
            with patch.dict(os.environ, {"LC_PACKER_STORE_PASSWORD": "password"}), contextlib.redirect_stderr(error):
                self.assertEqual(main(["build", "--config", str(config)]), 2)
            self.assertIn("脚本", error.getvalue())
            self.assertNotIn("source", error.getvalue())


if __name__ == "__main__":
    unittest.main()
