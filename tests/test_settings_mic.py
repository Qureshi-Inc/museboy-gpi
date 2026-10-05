import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
pygame_stub = sys.modules.setdefault("pygame", types.ModuleType("pygame"))
for name, value in {"K_LEFT": 1, "K_RIGHT": 2, "K_UP": 3, "K_DOWN": 4,
                    "K_TAB": 5, "K_BACKSPACE": 6, "K_RETURN": 7,
                    "K_KP_ENTER": 8, "K_ESCAPE": 9}.items():
    setattr(pygame_stub, name, value)
gpi_ui_stub = sys.modules.setdefault("gpi_ui", types.ModuleType("gpi_ui"))
for name in ("GlobalKeyWatcher", "W", "H", "BLACK", "WHITE", "ACCENT", "DIM", "DARK"):
    setattr(gpi_ui_stub, name, object())
local_models_stub = sys.modules.setdefault("local_models", types.ModuleType("local_models"))
local_models_stub.snapshot = lambda: {}
evdev_stub = sys.modules.setdefault("evdev", types.ModuleType("evdev"))
evdev_stub.ecodes = object()
spec = importlib.util.spec_from_file_location(
    "museboy_settings", ROOT / "apps" / "settings" / "main.py")
settings_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings_module)


class MicMeterTests(unittest.TestCase):
    def test_meter_is_zero_for_silence_and_rises_for_input(self):
        self.assertEqual(settings_module.mic_level([0] * 32), 0)
        self.assertGreater(settings_module.mic_level([12000] * 32), 50)

    def test_keyboard_cursor_tracks_column_then_row_and_types_highlighted_key(self):
        app = object.__new__(settings_module.SettingsApp)
        app.edit = {"title": "Wi-Fi password", "text": "", "kind": "password",
                    "upper": False, "symbols": False, "cursor": [0, 0]}
        app.keyboard_move(pygame_stub.K_RIGHT)
        self.assertEqual(app.edit["cursor"], [1, 0])
        app.keyboard_move(pygame_stub.K_DOWN)
        self.assertEqual(app.edit["cursor"], [1, 1])
        app.keyboard_move(pygame_stub.K_RETURN)
        self.assertEqual(app.edit["text"], "w")

    def test_keyboard_stops_at_edges_and_skips_hidden_control(self):
        app = object.__new__(settings_module.SettingsApp)
        app.edit = {"title": "Network name", "text": "", "kind": "ssid",
                    "upper": False, "symbols": False, "cursor": [0, 0]}
        app.keyboard_move(pygame_stub.K_LEFT)
        app.keyboard_move(pygame_stub.K_UP)
        self.assertEqual(app.edit["cursor"], [0, 0])
        app.edit["cursor"] = [4, 4]
        app.keyboard_move(pygame_stub.K_RIGHT)
        self.assertEqual(app.edit["cursor"], [6, 4])
        self.assertEqual(app._keyrow(4)[app.edit["cursor"][0]], "CANCEL")

    def test_open_wifi_join_error_includes_nmcli_reason(self):
        with patch.object(settings_module, "command",
                          return_value=(10, "Error: Activation failed: no access point")):
            result = settings_module.wifi_connect("Free Wi-Fi")
        self.assertFalse(result["ok"])
        self.assertIn("no access point", result["message"])

    def test_wifi_scan_reports_captive_portal_state(self):
        def fake_command(args, timeout=8, input_text=None):
            if args[1:4] == ["-t", "-e", "yes"]:
                return 0, "*:Guest WiFi:90:"
            if args[1:4] == ["-t", "-f", "NAME,TYPE"]:
                return 0, ""
            if "connectivity" in args:
                return 0, "portal"
            return 1, ""

        with patch.object(settings_module, "command", side_effect=fake_command):
            result = settings_module.wifi_scan()
        self.assertEqual(result["connectivity"], "portal")
        self.assertEqual(result["aps"][0]["ssid"], "Guest WiFi")
        self.assertFalse(result["aps"][0]["security"])

    def test_portal_row_launches_browser_action(self):
        app = object.__new__(settings_module.SettingsApp)
        app.row = 4
        app.wifi_connectivity = "portal"
        started = []
        app.start = lambda fn, *args: started.append((fn, args))
        app.wifi_select()
        self.assertEqual(started, [(settings_module.launch_wifi_portal, ())])


if __name__ == "__main__":
    unittest.main()
