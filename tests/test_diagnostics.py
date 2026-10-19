"""Read-only diagnostics with real archive fixtures and process-boundary doubles."""

from dataclasses import replace
import builtins
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from lc_packer.diagnostics import EnvironmentConfig, check_environment


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("java.exe", "keytool.exe", "aapt.exe", "zipalign.exe", "adb.exe", "assistant.exe", "store.jks"):
            (self.root / name).write_bytes(b"fixture")
        for name in ("apktool.jar", "apksigner.jar"):
            with zipfile.ZipFile(self.root / name, "w") as archive:
                archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nMain-Class: Fixture\n")
        self.template = self.root / "template.apk"
        self.make_template(b"\x0a\x14template")
        self.compiled = self.root / "script.lc"
        self.compiled.write_bytes(b"\x0a\x14compiled")
        self.config = EnvironmentConfig(
            java=self.root / "java.exe", apktool=self.root / "apktool.jar",
            aapt=self.root / "aapt.exe", zipalign=self.root / "zipalign.exe",
            apksigner=self.root / "apksigner.jar", adb=self.root / "adb.exe",
            assistant=self.root / "assistant.exe", template=self.template,
            compiled=self.compiled, keystore=self.root / "store.jks", alias="release",
            store_password="super-secret-store", key_password="super-secret-key",
            workspace=self.root, output=self.root / "result.apk", serial="selected-device",
        )
        self.responses = {}
        self.commands = []
        self.device_listing = "List of devices attached\nselected-device\tdevice product:test\n"
        self.abis = "arm64-v8a,armeabi-v7a\n"
        self.process = patch("lc_packer.diagnostics.subprocess.run", side_effect=self.run_process).start()
        self.addCleanup(patch.stopall)
        patch("lc_packer.diagnostics._assistant_version", return_value="3.7.2").start()
        patch("lc_packer.diagnostics._adb_server_available", return_value=True).start()
        patch.dict(os.environ, {}, clear=True).start()

    def make_template(self, payload, *, abis=("arm64-v8a",)):
        with zipfile.ZipFile(self.template, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"manifest")
            archive.writestr("classes.dex", b"dex")
            archive.writestr("assets/script.lc", payload)
            for abi in abis:
                archive.writestr(f"lib/{abi}/libfixture.so", b"library")

    def run_process(self, args, **kwargs):
        self.commands.append((args, kwargs))
        executable = Path(args[0]).name
        kind = ("apktool" if "apktool.jar" in " ".join(args) else
                "apksigner" if "apksigner.jar" in " ".join(args) else
                "devices" if "devices" in args else
                "abi" if "getprop" in args else executable)
        response = self.responses.get(kind)
        if isinstance(response, BaseException):
            raise response
        if response is not None:
            code, output = response
        else:
            code, output = {
                "java.exe": (0, 'openjdk version "17.0.19"\nOpenJDK Runtime Environment'),
                "apktool": (0, "3.0.3\n"), "apksigner": (0, "0.9\n"),
                "aapt.exe": (0, "Android Asset Packaging Tool, v0.2-123\n"),
                "zipalign.exe": (2, "Zip alignment utility\nUsage: zipalign [-f] <align> infile.zip outfile.zip\n"),
                "keytool.exe": (0, "release, PrivateKeyEntry, certificate fingerprint"),
                "adb.exe": (0, "Android Debug Bridge version 1.0.41\n"),
                "devices": (0, self.device_listing), "abi": (0, self.abis),
            }[kind]
        return subprocess.CompletedProcess(args, code, output, "")

    def report(self, **changes):
        return check_environment(replace(self.config, **changes))

    def item(self, key, **changes):
        return next(item for item in self.report(**changes).items if item.key == key)

    def test_ready_build_and_device_are_separate_and_json_export_omits_secrets(self):
        report = self.report()
        self.assertTrue(report.build_ready)
        self.assertTrue(report.device_ready)
        serialized = json.dumps(report.to_dict())
        for secret in (self.config.store_password, self.config.key_password):
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, repr(self.config))
        self.assertEqual(self.item("automatic_compilation").status, "warning")
        self.assertIn("自动", self.item("automatic_compilation").detail)
        self.assertRegex(self.item("automatic_compilation").detail, "尚未|不可用|暂不|不支持")
        self.assertFalse(self.item("automatic_compilation").blocking)

    def test_automatic_compilation_explains_chinese_manual_remedy_without_blocking_build(self):
        report = self.report()
        item = next(item for item in report.items if item.key == "automatic_compilation")
        self.assertEqual(item.status, "warning")
        self.assertFalse(item.blocking)
        self.assertTrue(report.build_ready)
        self.assertIn("自动", item.detail)
        self.assertRegex(item.detail, "尚未|不可用|暂不|不支持")
        self.assertRegex(item.remedy, "[\u4e00-\u9fff]")
        self.assertRegex(item.remedy, "手动|捕获")
        self.assertIn("LC", item.remedy.upper())

    def test_missing_and_directory_executable_paths_block_with_concrete_remedy(self):
        for path in (self.root / "missing.exe", self.root):
            item = self.item("java", java=path)
            self.assertEqual(item.status, "error")
            self.assertIn(str(path), item.path)
            self.assertTrue(item.remedy)
            self.assertFalse(self.report(java=path).build_ready)

    def test_java_older_than_17_is_rejected_for_verified_apktool_toolchain(self):
        self.responses["java.exe"] = (0, 'java version "1.8.0_392"')
        item = self.item("java")
        self.assertEqual(item.status, "error")
        self.assertIn("17", item.remedy)

    def test_secret_redaction_does_not_change_tool_success_decisions(self):
        report = self.report(store_password="17", key_password="PrivateKeyEntry")
        self.assertTrue(report.build_ready)

    def test_environment_without_any_selections_returns_report_instead_of_raising(self):
        report = check_environment(EnvironmentConfig())
        self.assertFalse(report.build_ready)
        self.assertFalse(report.device_ready)
        self.assertEqual(next(item for item in report.items if item.key == "signing").status, "error")

    def test_unconfigured_json_report_explains_each_check_and_remedy_in_chinese(self):
        report = check_environment(EnvironmentConfig()).to_dict()
        self.assertFalse(report["build_ready"])
        self.assertFalse(report["device_ready"])
        for item in report["items"]:
            with self.subTest(key=item["key"]):
                self.assertIn(item["status"], ("pass", "warning", "error"))
                self.assertRegex(item["detail"], "[\u4e00-\u9fff]")
                if item["remedy"]:
                    self.assertRegex(item["remedy"], "[\u4e00-\u9fff]")

    def test_existing_output_and_non_ascii_workspace_are_blocking(self):
        existing = self.root / "existing.apk"
        existing.write_bytes(b"keep")
        self.assertEqual(self.item("output", output=existing).status, "error")
        workspace = self.root / "中文目录"
        workspace.mkdir()
        self.assertEqual(self.item("workspace", workspace=workspace).status, "error")

    def test_matching_eflc_is_not_ready_when_app_cannot_build_it(self):
        self.make_template(b"eflc-template")
        self.compiled.write_bytes(b"eflc-compiled")
        report = self.report()
        self.assertFalse(report.build_ready)
        self.assertNotEqual(next(item for item in report.items if item.key == "compatibility").status, "pass")

    def test_process_failure_has_exit_code_and_redacts_child_output(self):
        self.responses["apktool"] = (7, "failure super-secret-store super-secret-key")
        item = self.item("apktool")
        self.assertEqual(item.status, "error")
        self.assertIn("7", item.detail)
        self.assertIn("failure", item.detail)
        self.assertRegex(item.detail, "[\u4e00-\u9fff]")
        self.assertRegex(item.remedy, "[\u4e00-\u9fff]")
        self.assertIn(str(self.config.apktool), item.path)
        self.assertIn("[REDACTED]", item.detail)
        self.assertNotIn("super-secret", json.dumps(self.report().to_dict()))

    def test_timeout_and_launch_errors_become_actionable_errors(self):
        for error in (subprocess.TimeoutExpired(["java"], 10), OSError("invalid executable")):
            self.responses["java.exe"] = error
            item = self.item("java")
            self.assertEqual(item.status, "error")
            self.assertTrue(item.remedy)

    def test_zipalign_nonzero_usage_is_success_but_crash_is_not(self):
        self.assertEqual(self.item("zipalign").status, "pass")
        self.responses["zipalign.exe"] = (1, "missing dependency DLL")
        self.assertEqual(self.item("zipalign").status, "error")

    def test_all_children_are_bounded_hidden_shell_free_and_never_receive_password_arguments(self):
        self.report()
        for args, kwargs in self.commands:
            self.assertGreater(kwargs["timeout"], 0)
            self.assertFalse(kwargs.get("shell", False))
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            if os.name == "nt":
                self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
            self.assertNotIn(self.config.store_password, args)
            self.assertNotIn(self.config.key_password, args)

    def test_store_password_is_child_only_and_key_password_check_limit_is_explicit(self):
        before = dict(os.environ)
        item = self.item("signing")
        command, options = next(pair for pair in self.commands if Path(pair[0][0]).stem == "keytool")
        index = command.index("-storepass:env")
        self.assertEqual(options["env"][command[index + 1]], self.config.store_password)
        self.assertEqual(dict(os.environ), before)
        self.assertIn("私钥密码", item.detail)
        self.assertRegex(item.detail, "尚未|未验证|无法确认")

    def test_signing_missing_alias_password_or_keystore_blocks_build(self):
        for changes in ({"alias": ""}, {"store_password": ""}, {"keystore": self.root / "absent.jks"}):
            item = self.item("signing", **changes)
            self.assertEqual(item.status, "error")
            self.assertTrue(item.blocking)

    def test_wrong_password_or_missing_alias_reports_keytool_error_safely(self):
        self.responses["keytool.exe"] = (1, "Alias release does not exist; super-secret-store")
        item = self.item("signing")
        self.assertEqual(item.status, "error")
        self.assertIn("别名", item.detail)
        self.assertIn("Alias release", item.detail)
        self.assertNotIn(self.config.store_password, item.detail)

    def test_wrong_keystore_password_has_chinese_context_and_preserves_keytool_output(self):
        output = "Keystore was tampered with, or password was incorrect"
        self.responses["keytool.exe"] = (1, output)
        item = self.item("signing")
        self.assertEqual(item.status, "error")
        self.assertIn("密码", item.detail)
        self.assertIn(output, item.detail)
        self.assertIn("1", item.detail)
        self.assertRegex(item.remedy, "[\u4e00-\u9fff]")

    def test_certificate_only_alias_is_not_accepted_as_signing_key(self):
        self.responses["keytool.exe"] = (0, "release, trustedCertEntry, fingerprint")
        self.assertEqual(self.item("signing").status, "error")

    def test_known_old_template_and_eflc_is_blocking_incompatibility(self):
        self.make_template(b"\x0a\x14old-payload")
        self.compiled.write_bytes(b"eflccompiled")
        item = self.item("compatibility")
        self.assertEqual(item.status, "error")
        self.assertTrue(item.blocking)
        self.assertIn("eflc", item.detail)
        self.assertIn("0a14", item.detail)

    def test_matching_known_headers_do_not_claim_runtime_execution(self):
        item = self.item("compatibility")
        self.assertEqual(item.status, "pass")
        self.assertRegex(item.detail, "执行|运行")
        self.assertRegex(item.detail, "不能|不代表|无法|未验证")

    def test_empty_unknown_or_placeholder_compiled_input_is_not_ready(self):
        for content in (b"", b"unknown-data", b"eflc"):
            self.compiled.write_bytes(content)
            self.assertNotEqual(self.item("compiled").status, "pass")
            self.assertFalse(self.report().build_ready)

    def test_unknown_and_empty_template_headers_are_unproven(self):
        for header in (b"", b"????"):
            self.make_template(header)
            item = self.item("compatibility")
            self.assertEqual(item.status, "warning")
            self.assertTrue(item.blocking)

    def test_bad_template_archive_has_actionable_blocking_error(self):
        self.template.write_bytes(b"invalid ZIP")
        item = self.item("template")
        self.assertEqual(item.status, "error")
        self.assertRegex(item.detail, "损坏|无效")
        self.assertIn("Invalid ZIP archive", item.detail)
        self.assertTrue(item.remedy)

    def test_assistant_unknown_metadata_warns_without_executing_selected_gui(self):
        with patch("lc_packer.diagnostics._assistant_version", return_value=None):
            item = self.item("assistant")
        self.assertEqual(item.status, "warning")
        self.assertFalse(item.blocking)
        self.assertFalse(any(Path(args[0]).stem == "assistant" for args, _ in self.commands))

    def test_workspace_output_errors_and_permission_estimates_are_read_only(self):
        before = sorted(self.root.iterdir())
        for changes in ({"workspace": self.root / "missing"}, {"output": self.root}, {"output": self.root / "absent" / "result.apk"}):
            self.assertFalse(self.report(**changes).build_ready)
        with patch("lc_packer.diagnostics.os.access", return_value=False):
            self.assertEqual(self.item("workspace").status, "error")
            self.assertEqual(self.item("output").status, "error")
        self.assertEqual(sorted(self.root.iterdir()), before)
        self.assertRegex(self.item("workspace").detail, "预检|估")

    def test_low_disk_space_is_a_blocking_preflight_warning(self):
        with patch("lc_packer.diagnostics.shutil.disk_usage", return_value=type("Usage", (), {"free": 1})()):
            item = self.item("workspace")
        self.assertEqual(item.status, "warning")
        self.assertTrue(item.blocking)

    def test_absent_adb_does_not_block_build_and_has_no_device_readiness(self):
        report = self.report(adb=None)
        self.assertTrue(report.build_ready)
        self.assertFalse(report.device_ready)
        self.assertEqual(next(item for item in report.items if item.key == "adb").status, "warning")

    def test_absent_selected_offline_and_unauthorized_devices_are_separate_warnings(self):
        for listing, expected in (("List of devices attached\n", "未找到"),
                                  ("selected-device\toffline\n", "offline"),
                                  ("selected-device\tunauthorized\n", "unauthorized")):
            self.device_listing = listing
            report = self.report()
            self.assertTrue(report.build_ready)
            self.assertFalse(report.device_ready)
            item = next(item for item in report.items if item.key == "device")
            self.assertIn(expected, item.detail.lower())

    def test_no_implicit_device_selection_even_if_one_is_online(self):
        self.assertFalse(self.report(serial=None).device_ready)

    def test_abi_mismatch_blocks_device_readiness_only(self):
        self.abis = "x86_64,x86\n"
        report = self.report()
        self.assertTrue(report.build_ready)
        self.assertFalse(report.device_ready)
        item = next(item for item in report.items if item.key == "device_abi")
        self.assertEqual(item.status, "warning")
        self.assertIn("x86_64", item.detail)
        self.assertIn("arm64-v8a", item.detail)

    def test_stopped_adb_server_is_not_automatically_started(self):
        with patch("lc_packer.diagnostics._adb_server_available", return_value=False):
            report = self.report()
        self.assertTrue(report.build_ready)
        self.assertFalse(report.device_ready)
        self.assertFalse(any("devices" in args for args, _ in self.commands))
        item = next(item for item in report.items if item.key == "device")
        self.assertIn("start-server", item.remedy)

    def test_qt_reports_binding_and_qt_versions_without_creating_an_application_or_widget(self):
        def forbidden_window(*args, **kwargs):
            raise AssertionError("diagnostics must not create a Qt application or widget")

        binding = ModuleType("PySide6")
        binding.__version__ = "6.9.1"
        binding.QtCore = SimpleNamespace(qVersion=lambda: "6.9.0")
        binding.QtWidgets = SimpleNamespace(QApplication=forbidden_window, QWidget=forbidden_window)
        with patch.dict("sys.modules", {"PySide6": binding}):
            report = self.report()
        item = next((item for item in report.items if item.key == "qt"), None)
        self.assertIsNotNone(item)
        self.assertEqual(item.status, "pass")
        self.assertFalse(item.blocking)
        self.assertIn("PySide6 6.9.1", item.detail)
        self.assertIn("Qt 6.9.0", item.detail)
        self.assertIn("6.9.1", item.version)
        self.assertIn("6.9.0", item.version)
        self.assertIn("平台插件", item.detail)
        self.assertIn("未", item.detail)

    def test_missing_pyside6_warns_without_blocking_offline_builds(self):
        with patch.dict("sys.modules", {"PySide6": None}):
            report = self.report()
        item = next((item for item in report.items if item.key == "qt"), None)
        self.assertIsNotNone(item)
        self.assertEqual(item.status, "warning")
        self.assertFalse(item.blocking)
        self.assertTrue(report.build_ready)
        self.assertIn("pip install PySide6", item.remedy)
        self.assertIn("CLI", item.remedy)

    def test_qt_binary_dependency_import_failures_are_actionable_and_redacted(self):
        original_import = builtins.__import__
        for error_type in (ImportError, OSError):
            with self.subTest(error_type=error_type.__name__):
                def broken_qt_import(name, *args, **kwargs):
                    if name == "PySide6":
                        raise error_type("DLL load failed: super-secret-store super-secret-key")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=broken_qt_import):
                    report = self.report()
                item = next((item for item in report.items if item.key == "qt"), None)
                self.assertIsNotNone(item)
                self.assertEqual(item.status, "warning")
                self.assertFalse(item.blocking)
                self.assertTrue(report.build_ready)
                self.assertIn("DLL load failed", item.detail)
                self.assertIn("pip install PySide6", item.remedy)
                self.assertNotIn("super-secret", json.dumps(report.to_dict()))


if __name__ == "__main__":
    unittest.main()
