import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
pygame_stub = sys.modules.setdefault("pygame", types.ModuleType("pygame"))
gpi_ui_stub = sys.modules.setdefault("gpi_ui", types.ModuleType("gpi_ui"))
for name, value in {"W": 640, "H": 480, "BLACK": (0, 0, 0), "WHITE": (255, 255, 255),
                    "DIM": (100, 100, 100), "DARK": (20, 20, 20)}.items():
    setattr(gpi_ui_stub, name, value)
bolt_stub = sys.modules.setdefault("bolt", types.ModuleType("bolt"))
bolt_stub.Bolt = type("Bolt", (), {})
requests_stub = sys.modules.setdefault("requests", types.ModuleType("requests"))
spec = importlib.util.spec_from_file_location("museboy_appmart", ROOT / "apps/appmart/main.py")
appmart = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appmart)


class AppMartInstallShareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_apps_dir = appmart.APPS_DIR
        appmart.APPS_DIR = self.tmp.name

    def tearDown(self):
        appmart.APPS_DIR = self.old_apps_dir
        self.tmp.cleanup()

    def make_app(self, app_id="sample"):
        folder = Path(self.tmp.name) / app_id
        folder.mkdir()
        manifest = {"id": app_id, "name": "Muse-made app", "description": "Muse wrote this",
                    "category": "tools", "version": 2, "author": "Muse",
                    "exec": "run.sh"}
        (folder / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (folder / "icon.png").write_bytes(b"fake png")
        manifest["_path"] = str(folder)
        return manifest

    def test_installed_state_survives_as_folder_and_turns_off_after_uninstall(self):
        app = self.make_app()
        self.assertTrue(appmart.is_app_installed("sample"))
        ok, message = appmart.uninstall_app(app)
        self.assertTrue(ok, message)
        self.assertFalse(appmart.is_app_installed("sample"))

    def test_core_app_cannot_be_removed(self):
        ok, message = appmart.uninstall_app({"id": "settings"})
        self.assertFalse(ok)
        self.assertIn("Core apps", message)

    def test_submission_overrides_muse_manifest_author_with_user_byline(self):
        app = self.make_app()
        appmart.SUBMIT_TOKEN = "test-only"
        appmart.SHOP_URL = "https://example.invalid"
        captured = {}

        class Response:
            status_code = 202

        def fake_post(url, **kwargs):
            captured["manifest"] = json.loads(kwargs["files"]["manifest"][1])
            return Response()

        requests_stub.post = fake_post
        result = appmart.submit_app(app, "  Moiz   Qureshi ")
        self.assertIn("Sent for review", result)
        self.assertEqual(captured["manifest"]["author"], "Moiz Qureshi")
        self.assertEqual(captured["manifest"]["description"], "Muse wrote this")
        self.assertEqual(captured["manifest"]["category"], "tools")


if __name__ == "__main__":
    unittest.main()
