#!/usr/bin/env python3
"""GPi Case 2 gamepad -> keyboard + mouse mapper.

Reads the Xbox-360-compatible gamepad exposed by the GPi Case 2 and emits
a uinput keyboard ("gpi-keyboard") so every app and Chromium get normal
key events, plus mouse movement/clicks for Chromium. Runs as a systemd
service; exits (and restarts) if the pad vanishes.

Mapping:
  D-pad / left stick -> arrows (pygame UI) + mouse movement (Chromium)
  A     -> Enter (pygame UI) + left-click (Chromium)
  B     -> Escape
  X     -> Tab
  Y     -> Y
  LB/RB -> PageUp / PageDown
  Start -> Space
  Select-> F13 (global "home" signal, watched by the launcher)
"""
import sys
import time
from evdev import InputDevice, UInput, ecodes, list_devices

GAMEPAD_NAME = "Microsoft X-Box 360 pad"

BTN_KEYMAP = {
    304: ecodes.KEY_ENTER,     # A
    305: ecodes.KEY_ESC,       # B
    307: ecodes.KEY_TAB,       # X
    308: ecodes.KEY_Y,         # Y
    310: ecodes.KEY_PAGEUP,    # LB
    311: ecodes.KEY_PAGEDOWN,  # RB
    314: ecodes.KEY_F13,       # Select -> home
    315: ecodes.KEY_SPACE,     # Start
}

ALL_KEYS = [
    ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
    ecodes.KEY_ENTER, ecodes.KEY_ESC, ecodes.KEY_TAB, ecodes.KEY_Y,
    ecodes.KEY_PAGEUP, ecodes.KEY_PAGEDOWN, ecodes.KEY_F13, ecodes.KEY_SPACE,
]

# Mouse buttons for the uinput device
MOUSE_BTNS = [ecodes.BTN_LEFT, ecodes.BTN_RIGHT]

STICK_DEADZONE = 12000
MOUSE_SPEED = 12  # pixels per D-pad press event
MOUSE_REPEAT_DELAY = 0.03  # seconds between moves when held


def find_gamepad():
    for path in list_devices():
        try:
            dev = InputDevice(path)
            if GAMEPAD_NAME in dev.name:
                return dev
        except OSError:
            continue
    return None


class AxisToKeys:
    """Turns one analog/hat axis into a pair of keys with press/release."""

    def __init__(self, ui, neg_key, pos_key):
        self.ui = ui
        self.neg_key = neg_key
        self.pos_key = pos_key
        self.state = 0

    def update(self, value):
        if value != self.state:
            if self.state == -1:
                self.ui.write(ecodes.EV_KEY, self.neg_key, 0)
            elif self.state == 1:
                self.ui.write(ecodes.EV_KEY, self.pos_key, 0)
            if value == -1:
                self.ui.write(ecodes.EV_KEY, self.neg_key, 1)
            elif value == 1:
                self.ui.write(ecodes.EV_KEY, self.pos_key, 1)
            self.ui.syn()
            self.state = value


def main():
    # Keyboard + mouse in one uinput device
    ui = UInput({ecodes.EV_KEY: ALL_KEYS + MOUSE_BTNS,
                 ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y]},
                name="gpi-keyboard")
    hat_x = AxisToKeys(ui, ecodes.KEY_LEFT, ecodes.KEY_RIGHT)
    hat_y = AxisToKeys(ui, ecodes.KEY_UP, ecodes.KEY_DOWN)
    stick_x = AxisToKeys(ui, ecodes.KEY_LEFT, ecodes.KEY_RIGHT)
    stick_y = AxisToKeys(ui, ecodes.KEY_UP, ecodes.KEY_DOWN)
    # Track D-pad held state for continuous mouse movement
    mouse_dx, mouse_dy = 0, 0
    # Y button toggles mouse mode: when ON, D-pad moves mouse and A clicks
    # (no arrow keys/Enter); when OFF, normal gamepad behavior.
    mouse_mode = False

    import select

    while True:
        dev = find_gamepad()
        if dev is None:
            time.sleep(1.0)
            continue
        try:
            dev.grab()  # exclusive: raw pad events don't leak to other readers
            while True:
                # Non-blocking read with timeout for continuous mouse movement
                r, _, _ = select.select([dev.fd], [], [], MOUSE_REPEAT_DELAY)
                if r:
                    for ev in dev.read():
                        if ev.type == ecodes.EV_KEY and ev.code in BTN_KEYMAP:
                            # Y toggles mouse mode (on press, not release).
                            # Back and Home always restore the normal controls
                            # before sending their key to the current app.
                            if ev.code == 308 and ev.value == 1:  # Y
                                mouse_mode = not mouse_mode
                                # Release D-pad state when toggling
                                mouse_dx, mouse_dy = 0, 0
                                hat_x.update(0)
                                hat_y.update(0)
                            elif ev.value == 1 and ev.code in (305, 314):
                                # Back (B) and Select (Home) must not leave the
                                # launcher in pointer mode after an app exits.
                                mouse_mode = False
                                mouse_dx, mouse_dy = 0, 0
                                hat_x.update(0)
                                hat_y.update(0)
                                stick_x.update(0)
                                stick_y.update(0)
                                ui.write(ecodes.EV_KEY, BTN_KEYMAP[ev.code], ev.value)
                                ui.syn()
                            elif mouse_mode and ev.code == 304:  # A in mouse mode
                                # Only click, no Enter
                                ui.write(ecodes.EV_KEY, ecodes.BTN_LEFT, ev.value)
                                ui.syn()
                            elif not mouse_mode:
                                # Normal mode: send the mapped key
                                ui.write(ecodes.EV_KEY, BTN_KEYMAP[ev.code], ev.value)
                                # A button also sends left-click for Chromium
                                # (only in normal mode; mouse mode handles it above)
                                if ev.code == 304:  # A
                                    ui.write(ecodes.EV_KEY, ecodes.BTN_LEFT, ev.value)
                                ui.syn()
                            # In mouse mode, ignore other buttons except Y and A
                        elif ev.type == ecodes.EV_ABS:
                            if ev.code == 16:      # D-pad X
                                if mouse_mode:
                                    mouse_dx = ev.value * MOUSE_SPEED
                                else:
                                    hat_x.update(ev.value)
                            elif ev.code == 17:    # D-pad Y
                                if mouse_mode:
                                    mouse_dy = ev.value * MOUSE_SPEED
                                else:
                                    hat_y.update(ev.value)
                            elif ev.code == 0:     # left stick X
                                v = -1 if ev.value < -STICK_DEADZONE else (
                                    1 if ev.value > STICK_DEADZONE else 0)
                                stick_x.update(v)
                            elif ev.code == 1:     # left stick Y
                                v = -1 if ev.value < -STICK_DEADZONE else (
                                    1 if ev.value > STICK_DEADZONE else 0)
                                stick_y.update(v)
                # Continuous mouse movement while D-pad held (mouse mode only)
                if mouse_mode and (mouse_dx or mouse_dy):
                    if mouse_dx:
                        ui.write(ecodes.EV_REL, ecodes.REL_X, mouse_dx)
                    if mouse_dy:
                        ui.write(ecodes.EV_REL, ecodes.REL_Y, mouse_dy)
                    ui.syn()
        except OSError:
            pass
        finally:
            # release everything so no key sticks when the pad drops
            try:
                for k in ALL_KEYS + MOUSE_BTNS:
                    ui.write(ecodes.EV_KEY, k, 0)
                ui.syn()
            except OSError:
                pass
            try:
                dev.ungrab()
            except Exception:
                pass
            mouse_dx, mouse_dy = 0, 0
            mouse_mode = False
        time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
