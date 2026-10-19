from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

from lc_packer.build import BuildError, BuildMetadata, BuildService, BuildTools, prepare_decoded
from lc_packer.compiler import CompiledScript
from lc_packer.process import ProcessError, run_process
from lc_packer.project import MqbProject
from lc_packer.signing import SigningConfig, sign_apk


class BackendTests(unittest.TestCase):
    def test_successful_verification_is_not_broken_by_password_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / "key.p12"
            key.write_bytes(b"fixture")
            tools = SimpleNamespace(java=Path("java"), zipalign=Path("zipalign"), apksigner_jar=Path("apksigner.jar"))
            def runner(args, **kwargs):
                return run_process([sys.executable, "-c", "print('Verifies')"], **kwargs)
            sign_apk(root / "unsigned.apk", root / "signed.apk", tools,
                     SigningConfig(key, "alias", "Verifies"), runner=runner)
    def test_existing_output_is_protected_before_any_tool_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing.apk"
            output.write_bytes(b"keep")
            tools = BuildTools(*(Path("missing") for _ in range(5)))
            service = BuildService(tools, workspace=Path(tmp), runner=lambda *a, **k: self.fail("must not run"))
            with self.assertRaises(BuildError):
                service.build(None, None, None, output, None, None)
            self.assertEqual(output.read_bytes(), b"keep")
    def test_failed_child_reports_stage_exit_code_and_redacts_secret(self):
        with self.assertRaises(ProcessError) as caught:
            run_process([sys.executable, "-c", "import sys; print('secret-value'); sys.exit(7)"],
                        stage="sign", secrets=("secret-value",))
        self.assertIn("sign", str(caught.exception))
        self.assertIn("7", str(caught.exception))
        self.assertNotIn("secret-value", str(caught.exception))

    def test_signing_repr_never_contains_password(self):
        config = SigningConfig(Path("test.p12"), "test", "store-secret", "key-secret")
        self.assertNotIn("store-secret", repr(config))
        self.assertNotIn("key-secret", repr(config))

    def test_resource_mapping_clears_stale_payload_and_preserves_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "res" / "values").mkdir(parents=True)
            (root / "META-INF").mkdir()
            (root / "META-INF" / "OLD.SF").write_bytes(b"old")
            for suffix in ("lc", "prop", "uip", "ui", "uis", "rtd", "atc", "info"):
                (root / "assets" / f"script.{suffix}").write_bytes(b"stale")
            (root / "assets" / "out_script.lc").write_bytes(b"stale")
            (root / "AndroidManifest.xml").write_text('''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.old.app"><application android:name=".App" android:label="@string/app_name"><activity android:name="Runtime"/><provider android:name="com.runtime.Provider" android:authorities="@string/authority"/></application></manifest>''')
            (root / "res" / "values" / "strings.xml").write_text('<resources><string name="app_name">Old</string><string name="authority">com.old.app.provider</string><string name="opaque">unchanged</string></resources>')
            (root / "apktool.yml").write_text('versionInfo:\n  versionCode: 1\n  versionName: 1.0\n')
            lc = root / "input.lc"
            lc.write_bytes(b"\x0a\x14Jun compiled")
            compiled = CompiledScript(lc, hashlib.sha256(lc.read_bytes()).hexdigest(), "precompiled")
            project = MqbProject("t.mq", b"source", b"prop-bytes", None, None, ())
            prepare_decoded(root, project, compiled, BuildMetadata("com.new.app", "测试", 2, "2.0"))
            self.assertEqual((root / "assets" / "script.prop").read_bytes(), b"prop-bytes")
            self.assertEqual((root / "assets" / "script.lc").read_bytes(), lc.read_bytes())
            for suffix in ("uip", "ui", "uis", "rtd", "atc"):
                self.assertEqual((root / "assets" / f"script.{suffix}").read_bytes(), b"")
            self.assertEqual((root / "assets" / "script.info").read_bytes(), b"stale")
            self.assertFalse((root / "META-INF" / "OLD.SF").exists())
            manifest = ET.parse(root / "AndroidManifest.xml").getroot()
            ns = "{http://schemas.android.com/apk/res/android}"
            self.assertEqual(manifest.attrib["package"], "com.new.app")
            self.assertEqual(manifest.find("application").attrib[ns + "name"], "com.old.app.App")
            self.assertEqual(manifest.find("application/activity").attrib[ns + "name"], "com.old.app.Runtime")
            self.assertIn("com.new.app.provider", (root / "res/values/strings.xml").read_text())
            lc.write_bytes(b"tampered")
            with self.assertRaises(BuildError):
                prepare_decoded(root, project, compiled, BuildMetadata("com.new.app", "测试", 2, "2.0"))


if __name__ == "__main__":
    unittest.main()
