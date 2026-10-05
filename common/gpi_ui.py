"""Shared helpers for GPi console apps."""
import threading
from evdev import InputDevice, ecodes, list_devices

UINPUT_NAME = "gpi-keyboard"
W, H = 640, 480

BLACK = (8, 8, 12)
WHITE = (235, 235, 240)
ACCENT = (255, 176, 64)
DIM = (130, 130, 142)
DARK = (24, 24, 32)


class GlobalKeyWatcher:
    """Watches the gpi uinput keyboard at kernel level.

    Works regardless of which window has focus, so Select (F12) can act as
    a global "home" key even when Chromium is fullscreen.
    Multiple watchers may read the same device simultaneously.
    """

    def __init__(self, on_key):
        self.on_key = on_key
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _find(self):
        for path in list_devices():
            try:
                dev = InputDevice(path)
                if dev.name == UINPUT_NAME:
                    return dev
            except OSError:
                continue
        return None

    def _run(self):
        dev = None
        while not self._stop.is_set():
            if dev is None:
                dev = self._find()
                if dev is None:
                    self._stop.wait(1.0)
                    continue
            try:
                for ev in dev.read_loop():
                    if self._stop.is_set():
                        return
                    if ev.type == ecodes.EV_KEY and ev.value == 1:
                        try:
                            self.on_key(ev.code)
                        except Exception:
                            pass
            except OSError:
                dev = None  # device vanished; rescan

    def stop(self):
        self._stop.set()
