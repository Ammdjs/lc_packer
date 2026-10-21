"""Real, offscreen Qt coverage for portable tools and local discovery."""
import json
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTableWidget

from lc_packer import app, gui
from lc_packer.diagnostics import CheckResult, EnvironmentReport


TOOL_FILES = {
    "java": "jdk17/bin/java.exe", "apktool": "apktool/apktool.jar",
    "aapt": "android-build-tools/aapt.exe", "zipalign": "android-build-tools/zipalign.exe",
    "apksigner": "android-build-tools/lib/apksigner.jar", "adb": "platform-tools/adb.exe",
}


def package(identifier, label, version, target):
    return SimpleNamespace(id=identifier, label=label, version=version, filename=identifier + ".zip",
                           sha256="0" * 64, size=1, kind="zip", target=target, prefix="",
                           required=(), settings={}, source_url="https://example.org/tool.zip",
                           license_url="https://example.org/license")


PACKAGES = (package("jdk17", "Java / JDK", "17.0.17", "jdk17"),
            package("apktool", "apktool", "3.0.3", "apktool"),
            package("build-tools", "Android 构建工具", "36.1.0", "android-build-tools"),
            package("platform-tools", "ADB 平台工具", "36.0.0", "platform-tools"))


class PortableToolsGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {name: str(self.root / "tools" / suffix) for name, suffix in TOOL_FILES.items()}
        for target in self.paths.values():
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        self.status_state = "missing"
        self.patches = [patch("lc_packer.app.ROOT", self.root), patch("lc_packer.gui.ROOT", self.root, create=True),
                        patch("lc_packer.app.installed_settings", return_value={}, create=True),
                        patch("lc_packer.gui.load_catalog", return_value=PACKAGES, create=True),
                        patch("lc_packer.gui.package_status", side_effect=self.status, create=True)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.window = gui.PackerWindow(app.default_settings(), self.root / "settings.json")
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        self.window.cancel.set()
        self.wait_done()
        self.window.close()

    def status(self, root, pkg):
        self.assertEqual(root, self.root)
        return SimpleNamespace(id=pkg.id, label=pkg.label, version=pkg.version,
                               state=self.status_state, detail="离线包就绪，可安装",
                               path=self.root / "tools" / pkg.target)

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(0.005)
        self.qt.processEvents()
        self.assertTrue(predicate(), "Qt worker did not reach the expected state")

    def wait_done(self):
        self.wait_until(lambda: not self.window.busy and
                        not getattr(self.window, "_pending_environment_check", False))

    def button(self, name):
        widget = self.window.findChild(QPushButton, name)
        self.assertIsNotNone(widget, "portable tool action is missing: " + name)
        return widget

    def table(self):
        widget = self.window.findChild(QTableWidget, "toolsTable")
        self.assertIsNotNone(widget, "portable tool status table is missing")
        return widget

    def report(self, detail):
        return EnvironmentReport((CheckResult("java", "pass", "Java", detail, "", True,
                                             self.paths["java"], "17"),))

    def test_installed_local_tools_take_priority_over_global_discovery(self):
        global_java = self.root / "global-java.exe"
        global_java.write_bytes(b"global")
        with patch("lc_packer.app.installed_settings", return_value=self.paths, create=True), \
                patch("lc_packer.app.shutil.which", return_value=str(global_java)):
            settings = app.default_settings()
        self.assertEqual({name: settings[name] for name in TOOL_FILES}, self.paths)

    def test_built_in_template_is_default_and_explicit_external_choice_is_retained(self):
        module = self.root / "lc_packer" / "app.py"
        template = module.parent / "assets/templates/root-510.apk"
        template.parent.mkdir(parents=True)
        template.write_bytes(b"public-template")
        with patch("lc_packer.app.__file__", str(module)):
            self.assertEqual(app.default_settings()["template"], str(template))
            config = self.root / "external.json"
            config.write_text(json.dumps({"template": "D:/custom/base.apk"}), encoding="utf-8")
            self.assertEqual(app.load_settings(config)["template"], "D:/custom/base.apk")

    def test_tools_page_shows_version_status_path_and_keeps_five_pages(self):
        self.assertEqual(self.window.pages.count(), 5)
        self.assertEqual(len(self.window.edits), 21)
        self.assertFalse(self.window.isVisible())
        self.window.go(2)
        self.wait_done()
        table = self.table()
        self.assertEqual(table.rowCount(), 4)
        contents = "\n".join(table.item(row, column).text() for row in range(4)
                             for column in range(table.columnCount()))
        for pkg in PACKAGES:
            self.assertIn(pkg.label, contents)
            self.assertIn(pkg.version, contents)
            self.assertIn(str(self.root / "tools" / pkg.target), contents)
        self.assertIn("未安装", contents)
        self.assertIn(str(self.root / "tools"), self.window.required(QLabel, "toolsInstallLocation").text())

    def test_each_tool_explains_its_purpose_requirement_version_and_default_path(self):
        purposes = {"java": ("运行", "keytool", "签名密钥", "必需"),
                    "apktool": ("解码", "重建", "必需"),
                    "aapt": ("包信息", "必需"),
                    "zipalign": ("对齐", "必需"),
                    "apksigner": ("签名", "验证", "必需"),
                    "adb": ("设备", "可选")}
        versions = {"java": "17.0.17", "apktool": "3.0.3", "aapt": "36.1.0",
                    "zipalign": "36.1.0", "apksigner": "36.1.0", "adb": "36.0.0"}
        for field, words in purposes.items():
            with self.subTest(field=field):
                label = self.window.findChild(QLabel, field + "Help")
                self.assertIsNotNone(label, "tools need visible help beside their path: " + field)
                self.assertTrue(label.wordWrap())
                for word in words:
                    self.assertIn(word, label.text())
                self.assertIn(versions[field], label.text())
                self.assertIn(self.paths[field], label.text())
                self.assertIn("默认", label.text())
                self.assertIn(words[0], self.window.edits[field].toolTip())
        java_help = self.window.findChild(QLabel, "javaHelp")
        self.assertIsNotNone(java_help, "JDK help needs to include the companion keytool location")
        self.assertIn(str(self.root / "tools/jdk17/bin/keytool.exe"), java_help.text())

    def test_each_package_status_keeps_useful_help_before_and_after_refresh(self):
        purposes = ("keytool", "重建", "签名", "设备")
        def check_help():
            table = self.table()
            for row, (pkg, purpose) in enumerate(zip(PACKAGES, purposes)):
                with self.subTest(package=pkg.id):
                    detail = table.item(row, 4)
                    self.assertIn(purpose, detail.text())
                    self.assertIn("用途", detail.toolTip())
                    self.assertIn("默认", detail.toolTip())
                    self.assertIn(pkg.version, detail.toolTip())
                    self.assertIn(str(self.root / "tools" / pkg.target), detail.toolTip())
        check_help()
        self.button("refreshToolsButton").click()
        self.wait_done()
        check_help()
        self.assertIn("离线包就绪，可安装", self.table().item(0, 4).text())

    def test_startup_never_installs_tools_and_refresh_updates_broken_status(self):
        self.window.close()
        with patch("lc_packer.gui.install_packages", side_effect=AssertionError("startup must not install"), create=True):
            self.window = gui.PackerWindow(app.default_settings(), self.root / "settings.json")
            self.qt.processEvents()
            self.assertFalse(self.window.busy)
            self.assertFalse(self.window.config_path.exists())
            self.status_state = "broken"
            self.button("refreshToolsButton").click()
            self.wait_done()
        self.assertEqual(self.table().item(0, 2).text(), "需要修复")

    def test_default_discovery_does_not_prevent_opening_if_catalog_is_invalid(self):
        with patch("lc_packer.app.installed_settings", side_effect=gui.ToolInstallError("无效清单")):
            settings = app.default_settings()
        self.assertEqual(len(settings), 21)

    def test_install_binds_six_fields_saves_without_secrets_and_rechecks_after_worker_finishes(self):
        detected = threading.Event()
        allow_report = threading.Event()
        observed = {}
        old = self.report("old environment")
        self.window.handle_result("report", (self.window.settings_revision, old))
        self.window.password.setText("store-secret")
        self.window.key_password.setText("key-secret")

        def install(root, **kwargs):
            self.assertEqual(root, self.root)
            self.assertIs(kwargs["cancel_event"], self.window.cancel)
            self.assertNotEqual(threading.get_ident(), threading.main_thread().ident)
            kwargs["log"]("正在解压离线工具")
            self.status_state = "installed"
            return self.paths

        def detect(config):
            observed["java"] = config.java
            detected.set()
            allow_report.wait(3)
            return self.report("fresh environment")

        try:
            with patch("lc_packer.gui.install_packages", side_effect=install, create=True), \
                    patch("lc_packer.gui.check_environment", side_effect=detect):
                self.button("installToolsButton").click()
                self.wait_until(detected.is_set)
                self.assertIsNone(self.window.report, "old environment must be invalidated before redetection")
                self.assertEqual({name: self.window.edits[name].text() for name in TOOL_FILES}, self.paths)
                self.assertEqual(observed["java"], Path(self.paths["java"]))
                saved = self.window.config_path.read_text(encoding="utf-8")
                self.assertNotIn("secret", saved)
                self.assertEqual(json.loads(saved)["adb"], self.paths["adb"])
                allow_report.set()
                self.wait_done()
        finally:
            allow_report.set()
        self.assertIn("fresh environment", self.window.report.items[0].detail)
        self.assertEqual(self.window.report_revision, self.window.settings_revision)
        self.assertIn("正在解压离线工具", self.window.logs.toPlainText())

    def test_install_failure_preserves_fields_and_reports_specific_error(self):
        self.window.edits["java"].setText("D:/external/java.exe")
        with patch("lc_packer.gui.install_packages", side_effect=ValueError("SHA256 校验失败：jdk17.zip"), create=True), \
                patch("lc_packer.gui.check_environment", side_effect=AssertionError("failed installation must not run detection")):
            self.button("installToolsButton").click()
            self.wait_done()
        self.assertEqual(self.window.edits["java"].text(), "D:/external/java.exe")
        self.assertIn("SHA256 校验失败", self.window.logs.toPlainText())
        self.assertEqual(self.window.status_label.property("state"), "error")
        self.assertFalse(self.window.config_path.exists())

    def test_cancel_install_is_forwarded_while_qt_remains_responsive(self):
        entered = threading.Event()
        cancelled = threading.Event()

        def install(root, *, cancel_event, **kwargs):
            entered.set()
            if cancel_event.wait(3):
                cancelled.set()
            raise ValueError("已取消工具安装")

        with patch("lc_packer.gui.install_packages", side_effect=install, create=True):
            self.button("installToolsButton").click()
            self.wait_until(entered.is_set)
            self.assertTrue(self.window.busy)
            self.assertFalse(self.button("installToolsButton").isEnabled())
            self.assertTrue(self.button("cancelInstallButton").isEnabled())
            self.button("cancelInstallButton").click()
            self.wait_done()
        self.assertTrue(cancelled.is_set())
        self.assertIn("已取消", self.window.logs.toPlainText())
        self.assertFalse(self.window.config_path.exists())

    def test_close_requests_install_cancel_without_destroying_running_worker(self):
        entered = threading.Event()

        def install(root, *, cancel_event, **kwargs):
            entered.set()
            cancel_event.wait(3)
            raise ValueError("已取消工具安装")

        with patch("lc_packer.gui.install_packages", side_effect=install, create=True):
            self.button("installToolsButton").click()
            self.wait_until(entered.is_set)
            thread = self.window._thread
            self.assertFalse(self.window.close())
            self.assertTrue(self.window.cancel.is_set())
            self.assertIs(self.window._thread, thread)
            self.wait_done()
        self.assertTrue(self.window.close())

    def test_catalog_error_is_visible_and_install_does_not_become_an_empty_success(self):
        self.window.close()
        with patch("lc_packer.gui.load_catalog", side_effect=ValueError("工具清单校验失败"), create=True):
            self.window = gui.PackerWindow(app.default_settings(), self.root / "settings.json")
            hint = self.window.findChild(QLabel, "toolsStatusHint")
            self.assertIsNotNone(hint, "catalog failures need a visible explanation")
            self.assertIn("工具清单校验失败", hint.text())
            with patch("lc_packer.gui.install_packages", side_effect=ValueError("工具清单校验失败"), create=True):
                self.button("installToolsButton").click()
                self.wait_done()
        self.assertIn("工具清单校验失败", self.window.logs.toPlainText())
        self.assertEqual(self.window.status_label.property("state"), "error")
        self.assertFalse(self.window.config_path.exists())


if __name__ == "__main__":
    unittest.main()
