import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
pygame_stub = sys.modules.setdefault("pygame", types.ModuleType("pygame"))
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


if __name__ == "__main__":
    unittest.main()
