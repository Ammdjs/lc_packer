"""Real offscreen Qt coverage for choosing bundled and external templates."""
import json
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

from lc_packer import app, gui


class TemplateGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "settings.json"
        self.window = gui.PackerWindow(app.default_settings(), self.config)
        self.addCleanup(self.window.close)

    def selector(self):
        combo = self.window.findChild(QComboBox, "builtinTemplateCombo")
        self.assertIsNotNone(combo, "bundled templates need an actual selection widget")
        return combo

    def description(self):
        label = self.window.findChild(QLabel, "builtinTemplateDescription")
        self.assertIsNotNone(label, "template choices need visible descriptions and validation scope")
        self.assertTrue(label.wordWrap())
        return label.text()

    def test_all_sixteen_templates_are_available_with_the_verified_default_selected(self):
        combo = self.selector()
        self.assertEqual(combo.count(), 16)
        self.assertEqual(combo.currentData(), "root-510")
        self.assertEqual(Path(self.window.settings()["template"]).name, "root-510.apk")
        self.assertIn("已验证", self.description())
        self.assertIn("雷电", self.description())
        self.assertIn("3.7.2", self.description())
        self.assertEqual(len(self.window.edits), 21)
        self.assertEqual(self.window.pages.count(), 5)
        self.assertFalse(self.window.isVisible())

    def test_selecting_another_template_updates_the_real_path_and_shows_unverified_scope(self):
        combo = self.selector()
        index = next(index for index in range(combo.count()) if combo.itemData(index) != "root-510")
        combo.setCurrentIndex(index)
        chosen = Path(self.window.settings()["template"])
        self.assertTrue(chosen.is_file())
        self.assertNotEqual(chosen.name, "root-510.apk")
        self.assertIn("未实测", self.description())
        self.assertIn(chosen.name, self.description())
        self.assertIn("ABI", self.description())
        self.assertIsNotNone(self.window.findChild(QPushButton, "templateBrowse"))
        button = self.window.findChild(QPushButton, "useBuiltinTemplateButton")
        self.assertIsNotNone(button, "the selected built-in template needs an explicit use action")
        self.window.edits["template"].setText("D:/external/base.apk")
        with patch("lc_packer.gui.template_path", side_effect=ValueError("模板 SHA-256 校验失败"), create=True):
            combo.setCurrentIndex(index)
        self.assertEqual(self.window.settings()["template"], "D:/external/base.apk")
        self.assertIn("校验失败", self.description())
        button.click()
        self.assertEqual(Path(self.window.settings()["template"]), chosen)
        self.assertNotEqual(self.window.status_label.property("state"), "error")

    def test_invalid_bundled_catalog_keeps_external_template_usable(self):
        settings = app.default_settings()
        settings["template"] = "D:/custom/base.apk"
        self.window.close()
        with patch("lc_packer.gui.load_templates", side_effect=ValueError("无效模板目录"), create=True):
            self.window = gui.PackerWindow(settings, self.config)
        self.assertEqual(self.window.settings()["template"], "D:/custom/base.apk")
        self.assertEqual(self.selector().count(), 0)
        self.assertIn("无效模板目录", self.description())
        self.assertTrue(self.window.required(QPushButton, "templateBrowse").isEnabled())

    def test_startup_retains_an_external_template_without_applying_the_first_builtin(self):
        settings = app.default_settings()
        settings["template"] = "D:/external/my-template.apk"
        self.window.close()
        self.window = gui.PackerWindow(settings, self.config)
        self.assertEqual(self.window.settings()["template"], "D:/external/my-template.apk")
        self.assertEqual(self.selector().currentIndex(), -1)
        self.assertIn("外部模板", self.description())

    def test_loading_external_configuration_preserves_path_and_save_uses_the_same_field(self):
        self.selector().setCurrentIndex(1)
        self.config.write_text(json.dumps({"template": "D:/custom/base.apkm"}), encoding="utf-8")
        with patch("lc_packer.gui.QFileDialog.getOpenFileName", return_value=(str(self.config), "")):
            self.window.required(QPushButton, "loadConfigButton").click()
        self.assertEqual(self.window.settings()["template"], "D:/custom/base.apkm")
        self.assertEqual(self.selector().currentIndex(), -1)
        self.assertIn("外部模板", self.description())
        self.window.required(QPushButton, "saveButton").click()
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["template"], "D:/custom/base.apkm")
        self.assertEqual(len(saved), 21)


if __name__ == "__main__":
    unittest.main()
