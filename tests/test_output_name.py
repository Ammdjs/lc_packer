"""Application-name edits keep APK output names in the chosen directory."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from lc_packer.app import default_settings, save_settings
from lc_packer.gui import PackerWindow


class OutputNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = PackerWindow(default_settings(), Path(self.temp.name) / "settings.json")
        self.addCleanup(self.window.close)

    def output(self):
        return Path(self.window.edits["output"].text())

    def test_name_edit_updates_apk_filename_and_keeps_desktop_directory(self):
        self.window.edits["output"].setText("C:/Users/Example/Desktop/旧名称.apk")
        self.window.edits["app_name"].setText("测试1.2")
        self.assertEqual(self.output(), Path("C:/Users/Example/Desktop/测试1.2.apk"))

    def test_later_name_edits_follow_a_newly_chosen_output_directory(self):
        self.window.edits["app_name"].setText("测试1.2")
        self.window.edits["output"].setText("E:\\发布目录\\自定义.apk")
        self.window.edits["app_name"].setText("测试1.3")
        self.assertEqual(self.output(), Path("E:/发布目录/测试1.3.apk"))

    def test_empty_name_during_edit_keeps_current_output(self):
        previous = self.output()
        self.window.edits["app_name"].clear()
        self.assertEqual(self.output(), previous)

    def test_name_with_path_characters_cannot_change_output_directory(self):
        self.window.edits["output"].setText("C:/Users/Example/Desktop/旧名称.apk")
        self.window.edits["app_name"].setText("测试/1:2?")
        self.assertEqual(self.output(), Path("C:/Users/Example/Desktop/测试_1_2_.apk"))
        self.assertEqual(self.window.edits["app_name"].text(), "测试/1:2?")

    def test_loading_configuration_preserves_its_explicit_output_name(self):
        settings = default_settings()
        settings.update(app_name="载入的应用", output="C:/Users/Example/Desktop/自定义文件名.apk")
        config = Path(self.temp.name) / "loaded.json"
        save_settings(config, settings)
        with patch("lc_packer.gui.QFileDialog.getOpenFileName", return_value=(str(config), "")):
            self.window.load_config()
        self.assertEqual(self.output(), Path(settings["output"]))
        self.window.edits["app_name"].setText("载入后修改")
        self.assertEqual(self.output(), Path("C:/Users/Example/Desktop/载入后修改.apk"))

    def test_window_uses_new_project_name(self):
        self.assertEqual(self.window.windowTitle(), "LC打包")
