import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from lc_packer.cli import main
from lc_packer.app import save_settings, load_settings


class CliTests(unittest.TestCase):
    def test_inspect_reports_project_without_executing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "test.mqb"
            with zipfile.ZipFile(project, "w") as archive:
                archive.writestr("test.mq", 'ShowMessage "ok"')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["inspect", str(project)]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["source_name"], "test.mq")

    def test_config_save_filters_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "settings.json"
            save_settings(config, {"source": "demo.mqb", "store_password": "SECRET", "key_password": "SECRET"})
            self.assertNotIn("SECRET", config.read_text())
            self.assertEqual(load_settings(config)["source"], "demo.mqb")

    def test_automatic_compilation_reports_missing_capability(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(main(["compile", "test.mqb"]), 2)
        self.assertIn("compiler CLI/API", error.getvalue())
