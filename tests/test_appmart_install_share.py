import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
pygame_stub = sys.modules.setdefault("pygame", types.ModuleType("pygame"))


class FakeIconSurface:
    def get_size(self):
        return (64, 64)


pygame_stub.image = types.SimpleNamespace(
    load=lambda path: FakeIconSurface(),
    save=lambda surface, path: Path(path).write_bytes(b"\x89PNG\r\n\x1a\nnormalized icon"))
pygame_stub.transform = types.SimpleNamespace(smoothscale=lambda surface, size: surface)
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
        # It has an image extension but deliberately does not contain PNG bytes.
        (folder / "icon.png").write_bytes(b"fake image bytes mislabeled as PNG")
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
            captured["icon"] = kwargs["files"]["icon"][1].read()
            with zipfile.ZipFile(kwargs["files"]["bundle"][1]) as bundle:
                captured["bundle_icon"] = bundle.read("icon.png")
            return Response()

        requests_stub.post = fake_post
        result = appmart.submit_app(app, "  Moiz   Qureshi ")
        self.assertIn("Sent for review", result)
        self.assertEqual(captured["manifest"]["author"], "Moiz Qureshi")
        self.assertEqual(captured["manifest"]["description"], "Muse wrote this")
        self.assertEqual(captured["manifest"]["category"], "tools")
        self.assertEqual(app["description"], "Muse wrote this")  # Installed app is untouched.
        self.assertTrue(captured["icon"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(captured["bundle_icon"].startswith(b"\x89PNG\r\n\x1a\n"))

    def test_saved_author_is_prefilled_and_persisted(self):
        old_dir, old_file = appmart.APP_MART_DATA_DIR, appmart.APP_MART_AUTHOR_FILE
        appmart.APP_MART_DATA_DIR = self.tmp.name
        appmart.APP_MART_AUTHOR_FILE = str(Path(self.tmp.name) / "author.json")
        try:
            appmart.save_author("  Moiz   Qureshi ")
            self.assertEqual(appmart.load_saved_author(), "Moiz Qureshi")
        finally:
            appmart.APP_MART_DATA_DIR, appmart.APP_MART_AUTHOR_FILE = old_dir, old_file

    def test_background_catalog_result_replaces_instant_local_shelf(self):
        mart = object.__new__(appmart.Appmart)
        mart.catalog_result = (["all", "games"], [{"id": "remote", "name": "Remote app"}])
        mart.catalog_loading = True
        mart.category_index = 0
        mart.shelf = appmart.demo_apps()
        mart.demo = True
        mart.sel = 0
        mart.icon_cache = {("hello", 104): object()}
        mart.apply_catalog_result()
        self.assertEqual(mart.shelf, [{"id": "remote", "name": "Remote app"}])
        self.assertEqual(mart.categories, ["all", "games"])
        self.assertFalse(mart.demo)
        self.assertFalse(mart.catalog_loading)
        self.assertFalse(mart.icon_cache)


if __name__ == "__main__":
    unittest.main()
