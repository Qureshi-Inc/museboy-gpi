#!/usr/bin/env python3
"""Controller-first settings for Muse Boy on the RetroFlag GPi Case 2.

No desktop keyboard is required: Wi-Fi SSIDs and passwords use the built-in
D-pad keyboard. Long-running scans and connections run off the UI thread.
"""
import array
import math
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import threading
import time

import pygame

sys.path.insert(0, "/opt/gpi/common")
from gpi_ui import GlobalKeyWatcher, W, H, BLACK, WHITE, ACCENT, DIM, DARK
from local_models import snapshot as local_model_snapshot
from evdev import ecodes

BG = (10, 14, 24)
PANEL = (21, 28, 44)
PANEL_HI = (49, 40, 28)
MUTED = (150, 160, 180)
CYAN = (107, 206, 232)
GREEN = (120, 220, 160)
RED = (250, 130, 120)


def mic_level(samples):
    """Map signed 16-bit PCM peak amplitude to a readable 0–100 meter."""
    peak = max((abs(int(sample)) for sample in samples), default=0)
    if peak < 1:
        return 0
    dbfs = 20 * math.log10(min(32768, peak) / 32768.0)
    return max(0, min(100, int((dbfs + 55) * 100 / 52)))


class MicMeter:
    """Brief live PulseAudio capture for input testing; samples are never saved."""
    def __init__(self):
        self.process = None
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.level = 0
        self.error = ""

    def start(self, source):
        self.stop()
        if not source:
            self.error = "No microphone selected"
            return False
        self.level = 0
        self.error = ""
        self.stop_event = threading.Event()
        try:
            self.process = subprocess.Popen(
                ["parec", "--device=" + source, "--format=s16le", "--rate=16000",
                 "--channels=1", "--raw"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except OSError as exc:
            self.error = "Mic monitor unavailable: " + str(exc)[:50]
            self.process = None
            return False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        return True

    def _read(self):
        proc = self.process
        while proc and not self.stop_event.is_set():
            try:
                block = proc.stdout.read(2048)
            except (OSError, ValueError):
                break
            if not block:
                break
            samples = array.array("h")
            samples.frombytes(block[:len(block) - (len(block) % 2)])
            value = mic_level(samples)
            with self.lock:
                self.level = value
        if proc and proc.poll() is not None and not self.stop_event.is_set():
            with self.lock:
                self.error = "Mic stream ended; check the selected input"

    def snapshot(self):
        with self.lock:
            return self.level, self.error, self.process is not None and self.process.poll() is None

    def stop(self):
        self.stop_event.set()
        proc, thread = self.process, self.thread
        self.process = self.thread = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        if thread and thread.is_alive():
            thread.join(timeout=1)
        with self.lock:
            self.level = 0


def font(size):
    for name in ("dejavusans", "freesans", "liberationsans"):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            pass
    return pygame.font.Font(None, size)


def command(args, timeout=8, input_text=None):
    try:
        p = subprocess.run(args, input=input_text, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


def select_local_model(model_id):
    rc, output = command(["sudo", "-n", "/usr/local/sbin/gpi-model-select",
                          model_id], timeout=240)
    return {"ok": rc == 0,
            "message": output[-180:] if output else
                       ("Model loaded" if rc == 0 else "Model switch failed")}


def store_muse_token(token):
    rc, output = command(["sudo", "-n", "/usr/local/sbin/gpi-muse-token"],
                         timeout=20, input_text=token + "\n")
    return {"ok": rc == 0, "token_saved": rc == 0,
            "message": output[-120:] if output else
                       ("SDK token saved securely" if rc == 0 else "Could not save token")}


def start_muse_pairing():
    rc, output = command(["sudo", "-n", "/usr/local/sbin/gpi-muse-pair"], timeout=12)
    return {"ok": rc == 0, "pairing": True,
            "message": output[-150:] if output else
                       ("Pairing opened. In Muse, go to Settings > Devices > Add Device."
                        if rc == 0 else "Could not open Muse pairing")}


def muse_token_status():
    rc, output = command(["sudo", "-n", "/usr/local/sbin/gpi-muse-token-status"], timeout=5)
    return rc == 0 and output.strip() == "configured"


class Worker:
    """One background task at a time; the pygame thread only polls results."""
    def __init__(self):
        self.thread = None
        self.running = False
        self.result = None
        self.error = None
        self.lock = threading.Lock()

    def start(self, fn, *args):
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.result = None
            self.error = None

        def run():
            try:
                result = fn(*args)
                with self.lock:
                    self.result = result
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
            finally:
                with self.lock:
                    self.running = False

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return True

    def take(self):
        with self.lock:
            if self.running:
                return None, None, False
            result, error = self.result, self.error
            self.result = self.error = None
            return result, error, result is not None or error is not None


def _nmcli_fields(line):
    """Split nmcli's escaped terse output (SSID may contain colons)."""
    fields, cur, escaped = [], [], False
    for ch in line:
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def wifi_scan():
    rc, out = command(["nmcli", "-t", "-e", "yes", "-f",
                       "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi",
                       "list", "--rescan", "yes"], timeout=18)
    if rc:
        return {"aps": [], "connectivity": "unknown",
                "error": " ".join(out.split())[:100] or "Wi-Fi scan failed"}
    saved = set()
    rc, profiles = command(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    if rc == 0:
        for profile in profiles.splitlines():
            fields = _nmcli_fields(profile)
            if len(fields) >= 2 and fields[1] == "802-11-wireless":
                _, ssid = command(["nmcli", "-g", "802-11-wireless.ssid",
                                   "connection", "show", fields[0]])
                if ssid:
                    saved.add(ssid.strip())
    aps = {}
    for line in out.splitlines():
        row = _nmcli_fields(line)
        if len(row) < 4 or not row[1]:
            continue
        try:
            signal = int(row[2])
        except ValueError:
            signal = 0
        ssid, security = row[1], row[3]
        item = {"ssid": ssid, "signal": signal,
                "security": security, "connected": row[0] == "*",
                "known": ssid in saved}
        if ssid not in aps or signal > aps[ssid]["signal"] or item["connected"]:
            aps[ssid] = item
    return {"aps": sorted(aps.values(), key=lambda ap: (not ap["connected"], -ap["signal"], ap["ssid"])),
            "connectivity": get_network_connectivity()}


def get_network_connectivity():
    """Ask NetworkManager whether Wi-Fi has internet or needs portal sign-in."""
    rc, out = command(["nmcli", "networking", "connectivity", "check"], timeout=12)
    state = out.strip().lower()
    return state if rc == 0 and state in {"none", "portal", "limited", "full", "unknown"} else "unknown"


def accept_direction_press(key, held):
    """Ignore repeated KEYDOWN events until the physical direction is released."""
    directions = {pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT}
    if key not in directions:
        return True
    if key in held:
        return False
    held.add(key)
    return True


def launch_wifi_portal():
    """Open an HTTP page Chromium can let a captive Wi-Fi portal intercept."""
    browser = shutil.which("chromium") or shutil.which("chromium-browser")
    if not browser:
        return {"portal_browser": True, "ok": False,
                "message": "Wi-Fi browser is missing; reinstall MuseBoy with browser support."}
    try:
        subprocess.Popen(
            [browser, "--kiosk", "--no-first-run", "--no-default-browser-check",
             "--noerrdialogs", "--user-data-dir=/home/tendo/.config/gpi-wifi-portal",
             "http://neverssl.com/"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True)
        return {"portal_browser": True, "ok": True,
                "message": "Accept the Wi-Fi terms in the browser, then press Select for Home."}
    except OSError:
        return {"portal_browser": True, "ok": False,
                "message": "Could not open the Wi-Fi sign-in browser."}


def wifi_connect(ssid, password=None, hidden=False):
    """Connect without putting the Wi-Fi secret in argv or a shell command."""
    base = ["nmcli", "--wait", "30", "device", "wifi", "connect", ssid,
            "ifname", "wlan0"]
    if hidden:
        base += ["hidden", "yes"]
    if not password:
        rc, out = command(base, timeout=35)
        detail = " ".join(out.split())[:58]
        return {"ok": rc == 0, "message": "Joined " + ssid + ". Checking access…" if rc == 0
                else "Wi-Fi join failed: " + (detail or "check signal and try again")}

    # --ask reads the key from a pseudo-terminal; the secret never appears in
    # process arguments, shell history, the screen, or application logs.
    master, slave = pty.openpty()
    proc = None
    output = bytearray()
    sent = False
    try:
        ask_args = ["nmcli", "--ask", "--wait", "30", "device", "wifi",
                    "connect", ssid, "ifname", "wlan0"]
        if hidden:
            ask_args += ["hidden", "yes"]
        proc = subprocess.Popen(ask_args, stdin=slave,
                                stdout=slave, stderr=slave, close_fds=True,
                                start_new_session=True)
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + 36
        while time.monotonic() < deadline and proc.poll() is None:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                output.extend(chunk)
                if not sent and re.search(rb"(?i)(password|secret).*(?:\r?\n|:)", output[-1024:]):
                    os.write(master, password.encode("utf-8") + b"\n")
                    sent = True
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        # Drain final status text; never return it because it can echo input.
        try:
            while select.select([master], [], [], 0)[0]:
                output.extend(os.read(master, 4096))
        except OSError:
            pass
        ok = proc.returncode == 0 and sent
        return {"ok": ok, "message": "Connected to " + ssid if ok else
                "Could not connect. Check the password and try again."}
    finally:
        if slave >= 0:
            os.close(slave)
        os.close(master)


def bluetooth_devices(scan=False):
    if scan:
        command(["bluetoothctl", "power", "on"], timeout=6)
        command(["bluetoothctl", "--timeout", "9", "scan", "on"], timeout=12)
    rc, out = command(["bluetoothctl", "devices"], timeout=6)
    if rc:
        return {"error": "Bluetooth is unavailable"}
    found = {}
    for line in out.splitlines():
        match = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line.strip())
        if match:
            found[match.group(1)] = match.group(2).strip()
    items = []
    for mac, name in found.items():
        _, info = command(["bluetoothctl", "info", mac], timeout=5)
        items.append({"mac": mac, "name": name,
                      "paired": "Paired: yes" in info,
                      "connected": "Connected: yes" in info,
                      "trusted": "Trusted: yes" in info})
    return {"devices": sorted(items, key=lambda d: (not d["connected"], d["name"].lower())),
            "powered": True if scan else None}


def bluetooth_action(action, mac=None):
    if action in ("power on", "power off"):
        rc, _ = command(["bluetoothctl", "power", action.split()[1]], timeout=8)
        state = action.split()[1]
        return {"ok": rc == 0, "message": "Bluetooth " + state,
                "powered": state == "on" if rc == 0 else None}
    if not mac:
        return {"ok": False, "message": "Select a device first"}
    if action == "connect":
        command(["bluetoothctl", "trust", mac], timeout=8)
        rc, out = command(["bluetoothctl", "connect", mac], timeout=20)
    elif action == "disconnect":
        rc, out = command(["bluetoothctl", "disconnect", mac], timeout=10)
    else:
        command(["bluetoothctl", "--agent", "NoInputNoOutput", "agent", "on"], timeout=5)
        rc, out = command(["bluetoothctl", "--agent", "NoInputNoOutput", "pair", mac], timeout=25)
        if rc == 0 or "Pairing successful" in out:
            command(["bluetoothctl", "trust", mac], timeout=8)
            rc, out = command(["bluetoothctl", "connect", mac], timeout=20)
    ok = rc == 0 or "successful" in out.lower() or "already connected" in out.lower()
    return {"ok": ok, "message": ("Connected" if action != "disconnect" else "Disconnected") if ok else "Pair/connect failed. Put the device in pairing mode and retry."}


def audio_snapshot():
    _, sinks = command(["pactl", "list", "short", "sinks"])
    _, sources = command(["pactl", "list", "short", "sources"])
    _, info = command(["pactl", "info"])
    _, output_level = command(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    _, input_level = command(["pactl", "get-source-volume", "@DEFAULT_SOURCE@"])
    defaults = {}
    for line in info.splitlines():
        if line.startswith("Default Sink:"):
            defaults["sink"] = line.split(":", 1)[1].strip()
        if line.startswith("Default Source:"):
            defaults["source"] = line.split(":", 1)[1].strip()
    def parse(rows):
        result = []
        for line in rows.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                if parts[1].endswith(".monitor"):
                    continue
                result.append({"id": parts[0], "name": parts[1],
                               "label": audio_label(parts[1])})
        return result
    def level(text, fallback):
        match = re.search(r"(\d+)%", text)
        return int(match.group(1)) if match else fallback
    return {"sinks": parse(sinks), "sources": parse(sources),
            "defaults": defaults, "volume_out": level(output_level, 50),
            "volume_in": level(input_level, 70)}


def audio_label(name):
    n = name.lower()
    if "a50_x" in n or "a50-x" in n:
        return "Astro A50 X"
    if "generalplus" in n or "usb_audio_device" in n:
        return "USB audio / dock mic"
    if "hdmi" in n:
        return "HDMI display"
    if "monitor" in n:
        return "Monitor of " + audio_label(name.replace(".monitor", ""))
    return name.replace("alsa_output.", "").replace("alsa_input.", "").replace("-analog-stereo", "").replace("-mono-fallback", "")[:28]


def device_snapshot():
    usb = []
    for path in sorted(__import__("glob").glob("/sys/bus/usb/devices/*/product")):
        try:
            name = open(path).read().strip()
            if name and name not in usb and name not in ("USB2.0 Hub", "USB2.0 HUB", "DWC OTG Controller"):
                usb.append(name)
        except OSError:
            pass
    cameras = []
    for path in sorted(__import__("glob").glob("/sys/class/video4linux/video*/name")):
        try:
            name = open(path).read().strip()
            if name and not name.startswith(("bcm2835", "rpi-hevc")):
                node = "/dev/video" + path.split("video")[1].split("/")[0]
                cameras.append((node, name))
        except OSError:
            pass
    return {"usb": usb, "cameras": cameras}


class SettingsApp:
    def __init__(self):
        pygame.init()
        pygame.mouse.set_visible(False)
        self.screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
        pygame.display.set_caption("Settings")
        self.title = font(28)
        self.item = font(19)
        self.small = font(15)
        self.tiny = font(13)
        self.menu = ["Wi-Fi", "Bluetooth", "Audio", "Cameras & USB", "Local AI", "System"]
        self.menu_hints = ["Choose a network and enter its password",
                           "Pair headsets, controllers, and other devices",
                           "Select the mic and speakers Tendo should use",
                           "See cameras and connected USB accessories",
                           "See and switch the on-device planner model",
                           "Device, network, and battery information"]
        self.sel = 0
        self.page = "main"
        self.row = 0
        self.worker = Worker()
        self.watcher = GlobalKeyWatcher(self._on_global_key)
        self.status = ""
        self.status_until = 0
        self.wifi_aps = []
        self.wifi_scanning = False
        self.wifi_connectivity = "unknown"
        self.pending_wifi_radio = None
        self.wifi_ssid = ""
        self.wifi_hidden = False
        self.wifi_password = ""
        self.wifi_radio = True
        self.bt_devices = []
        self.bt_powered = True
        self.audio = {"sinks": [], "sources": [], "defaults": {}}
        self.audio_row = 0
        self.volume_out = 50
        self.volume_in = 70
        self.mic_meter = MicMeter()
        self.devices = {"usb": [], "cameras": []}
        self.local_ai = {"models": [], "loaded": None,
                         "configured": None, "running": False}
        self.muse_token_configured = False
        self.edit = None
        self.restore_focus()

    def _on_global_key(self, code):
        # Home/Select is owned by the launcher, including while this app runs.
        pass

    def restore_focus(self):
        try:
            wid = pygame.display.get_wm_info().get("window")
            if wid:
                subprocess.run(["xdotool", "windowfocus", "--sync", str(wid)],
                               timeout=2, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def say(self, message, seconds=4):
        self.status = message
        self.status_until = time.monotonic() + seconds

    def start(self, fn, *args):
        if not self.worker.start(fn, *args):
            self.say("Please wait for the current task to finish")
            return False
        return True

    def poll_worker(self):
        result, error, done = self.worker.take()
        if not done:
            return
        # A scan can take several seconds. Honor a radio-toggle press as soon
        # as it exits instead of dropping the action because the worker is busy.
        if self.pending_wifi_radio is not None:
            state = self.pending_wifi_radio
            self.pending_wifi_radio = None
            self.say("Turning Wi-Fi " + state + "…", 12)
            self.start(wifi_radio, state)
            return
        if error:
            self.say("That action failed. Check the device and try again")
            return
        if self.page == "wifi" and isinstance(result, dict) and "portal_browser" in result:
            self.say(result["message"], 20)
        elif self.page == "wifi" and isinstance(result, dict) and "aps" in result:
            self.wifi_aps = result["aps"]
            self.wifi_connectivity = result.get("connectivity", "unknown")
            if result.get("error"):
                self.say(result["error"][:52], 12)
            elif self.wifi_connectivity == "portal":
                self.say("Wi-Fi joined. Sign-in is required.", 20)
            elif self.wifi_connectivity == "full":
                self.say("Internet is ready. Found %d networks" % len(self.wifi_aps))
            else:
                self.say("Found %d networks" % len(self.wifi_aps))
        elif self.page == "wifi" and isinstance(result, dict) and "radio" in result:
            self.wifi_radio = result["radio"] in ("on", "enabled")
            self.say(result["message"])
            if self.wifi_radio:
                self.start(wifi_scan)
            else:
                self.wifi_aps = []
                self.wifi_connectivity = "none"
        elif self.page == "wifi" and isinstance(result, dict) and "ok" in result:
            self.say(result["message"], 8)
            if result["ok"]:
                self.start(wifi_scan)
        elif self.page == "wifi" and isinstance(result, dict) and "error" in result:
            self.say(result["error"][:52])
        elif self.page == "bluetooth" and isinstance(result, dict) and "devices" in result:
            self.bt_devices = result["devices"]
            if result.get("powered") is not None:
                self.bt_powered = result["powered"]
            self.say("Found %d devices" % len(self.bt_devices))
        elif self.page == "bluetooth" and isinstance(result, dict) and "token_saved" in result:
            self.muse_token_configured = result["token_saved"]
            self.say(result["message"], 10)
        elif self.page == "bluetooth" and isinstance(result, dict) and "pairing" in result:
            self.say(result["message"], 20)
        elif self.page == "bluetooth" and isinstance(result, dict) and "ok" in result:
            if result.get("powered") is not None:
                self.bt_powered = result["powered"]
            self.say(result["message"])
            self.start(bluetooth_devices, False)
        elif self.page == "audio" and isinstance(result, dict) and "sinks" in result:
            self.audio = result
            self.volume_out = result.get("volume_out", self.volume_out)
            self.volume_in = result.get("volume_in", self.volume_in)
        elif self.page == "system" and isinstance(result, dict) and "host" in result:
            self.system = result
        elif self.page == "local_ai" and isinstance(result, dict) and "models" in result:
            self.local_ai = result
            if result.get("switch_message"):
                self.say(result["switch_message"], 12)
            elif result.get("running"):
                self.say("Model ready: " + str(result.get("loaded"))[:40])
        elif self.page == "local_ai" and isinstance(result, dict) and "ok" in result:
            self.say(result.get("message", "Model switch finished"), 12)
            self.start(local_model_snapshot)
        elif isinstance(result, dict) and "error" in result:
            self.say(result["error"][:52])
        elif isinstance(result, dict) and "ok" in result:
            self.say(result["message"])

    def open_page(self, page):
        self.page, self.row = page, 0
        self.status = ""
        if page == "wifi":
            rc, out = command(["nmcli", "radio", "wifi"])
            self.wifi_radio = out.strip() == "enabled" and rc == 0
            self.wifi_aps = []
            self.say("Scanning nearby networks…", 20)
            # Let the scan report whether the radio is unavailable. A separate
            # `nmcli radio` probe can be stale or localized and hid all SSIDs.
            self.start(wifi_scan)
        elif page == "bluetooth":
            self.bt_devices = []
            self.say("Loading saved devices…", 20)
            self.start(bluetooth_devices, False)
            rc, out = command(["bluetoothctl", "show"])
            self.bt_powered = "Powered: yes" in out
            self.muse_token_configured = muse_token_status()
        elif page == "audio":
            self.start(audio_snapshot)
        elif page == "devices":
            self.devices = device_snapshot()
        elif page == "system":
            self.start(system_snapshot)
        elif page == "local_ai":
            self.local_ai = {"models": [], "loaded": None,
                             "configured": None, "running": False}
            self.say("Checking local model service…", 30)
            self.start(local_model_snapshot)

    def header(self, title, sub=""):
        self.screen.fill(BG)
        pygame.draw.rect(self.screen, (17, 24, 39), (0, 0, W, 62))
        self.screen.blit(self.title.render(title, True, ACCENT), (20, 12))
        if sub:
            label = self.small.render(sub[:58], True, MUTED)
            self.screen.blit(label, (W - label.get_width() - 18, 22))

    def footer(self, hint="A choose   B back   Select home"):
        y = H - 27
        if self.status and time.monotonic() < self.status_until:
            txt = self.small.render(self.status[:66], True, CYAN)
            self.screen.blit(txt, (18, y - 26))
        pygame.draw.line(self.screen, (39, 49, 68), (16, y - 8), (W - 16, y - 8), 1)
        self.screen.blit(self.tiny.render(hint, True, MUTED), (18, y))

    def draw_main(self):
        self.header("Settings", "GPi Control Center")
        connected = next((x["ssid"] for x in self.wifi_aps if x["connected"]), "Wi-Fi ready")
        strip = pygame.Rect(18, 74, W - 36, 42)
        pygame.draw.rect(self.screen, PANEL, strip, border_radius=10)
        self.screen.blit(self.small.render("●  " + connected[:45], True, GREEN), (30, 87))
        for i, (name, hint) in enumerate(zip(self.menu, self.menu_hints)):
            col, row = i % 2, i // 2
            rect = pygame.Rect(18 + col * 306, 130 + row * 95, 294, 82)
            pygame.draw.rect(self.screen, PANEL_HI if i == self.sel else PANEL, rect, border_radius=12)
            pygame.draw.rect(self.screen, ACCENT if i == self.sel else (48, 58, 78), rect, 2 if i == self.sel else 1, border_radius=12)
            self.screen.blit(self.item.render(name, True, WHITE), (rect.x + 15, rect.y + 13))
            self.screen.blit(self.tiny.render(hint[:36], True, MUTED), (rect.x + 15, rect.y + 44))
        self.footer("D-pad move   A open   B back   Select home")

    def rows(self, title, rows, subtitle=""):
        self.header(title, subtitle)
        top, height = 76, 47
        view = max(1, (H - 127) // height)
        start = max(0, min(self.row - view + 1, len(rows) - view))
        for i in range(start, min(start + view, len(rows))):
            y = top + (i - start) * height
            rect = pygame.Rect(16, y, W - 32, 40)
            active = i == self.row
            pygame.draw.rect(self.screen, PANEL_HI if active else PANEL, rect, border_radius=8)
            if active:
                pygame.draw.rect(self.screen, ACCENT, rect, 2, border_radius=8)
            label, detail = rows[i]
            color = WHITE if active else (204, 211, 225)
            self.screen.blit(self.item.render(label[:43], True, color), (rect.x + 12, rect.y + 10))
            if detail:
                d = self.small.render(detail[:28], True, CYAN if active else MUTED)
                self.screen.blit(d, (rect.right - d.get_width() - 12, rect.y + 12))
        if not rows:
            self.screen.blit(self.item.render("Nothing found yet", True, MUTED), (24, 100))
        self.footer()

    def draw_wifi(self):
        rows = [("Wi-Fi radio", "On" if self.wifi_radio else "Off"),
                ("Scan again", "Nearby networks"),
                ("Join hidden network…", "Type its name")]
        connectivity = {"full": "Internet ready", "portal": "Sign-in required",
                        "limited": "Joined · no internet", "none": "Not connected",
                        "unknown": "Checking access"}.get(self.wifi_connectivity, "Checking access")
        rows.append(("Network access", connectivity))
        if self.wifi_connectivity == "portal":
            rows.append(("Open Wi-Fi sign-in", "Accept network terms"))
        for ap in self.wifi_aps:
            security = "Open" if not ap["security"] else ap["security"]
            state = "Connected · " if ap["connected"] else ("Saved · " if ap["known"] else "")
            rows.append((ap["ssid"], state + "%d%% · %s" % (ap["signal"], security)))
        self.rows("Wi-Fi", rows, "wlan0 · NetworkManager")

    def draw_bluetooth(self):
        rows = [("Bluetooth radio", "On" if self.bt_powered else "Off"),
                ("Scan for devices", "Pair headsets and controllers"),
                ("Muse SDK token · one time", "Stored securely" if self.muse_token_configured
                 else "Needed before BLE pairing"),
                ("Pair this GPi with Muse", "Open BLE pairing for 10 minutes")]
        for d in self.bt_devices:
            state = "Connected" if d["connected"] else ("Paired" if d["paired"] else "New device")
            rows.append((d["name"], state))
        self.rows("Bluetooth", rows, "Muse app: Settings > Devices > Add Device")

    def draw_audio(self):
        sinks, sources = self.audio.get("sinks", []), self.audio.get("sources", [])
        defaults = self.audio.get("defaults", {})
        level, error, running = self.mic_meter.snapshot()
        if error:
            meter_detail = "ERROR · " + error
        elif running:
            meter_detail = "%d%% %s" % (level, "#" * min(10, round(level / 10)) or "speak")
        else:
            meter_detail = "A to listen · not recorded"
        rows = [("Speaker / output", "Choose a device")]
        rows += [("  " + x["label"], "Current" if x["name"] == defaults.get("sink") else "Use") for x in sinks]
        rows += [("Microphone / input", "Choose a device")]
        rows += [("  " + x["label"], "Current" if x["name"] == defaults.get("source") else "Use") for x in sources]
        rows += [("Output volume", "%d%%  ◀ / ▶" % self.volume_out),
                 ("Input gain", "%d%%  ◀ / ▶" % self.volume_in),
                 ("Mic level check", meter_detail)]
        self.rows("Audio", rows, "Live mic meter · no audio saved")

    def draw_devices(self):
        rows = [("USB accessory", name) for name in self.devices["usb"]]
        rows += [("Camera", name + "  " + node) for node, name in self.devices["cameras"]]
        if not self.devices["cameras"]:
            rows.append(("Camera", "No external camera detected"))
        self.rows("Cameras & USB", rows, "Live hardware inventory")

    def draw_system(self):
        info = getattr(self, "system", {})
        rows = [("Device", info.get("host", "GPi CM4")),
                ("Wi-Fi", info.get("wifi", "Checking…")),
                ("IP address", info.get("ip", "Checking…")),
                ("Temperature", info.get("temp", "Unavailable")),
                ("Memory", info.get("memory", "Unavailable")),
                ("Battery", info.get("battery", "Checking…"))]
        self.rows("System", rows, "Status and diagnostics")

    def draw_local_ai(self):
        info = self.local_ai
        loaded = info.get("loaded")
        configured = info.get("configured")
        if loaded:
            service = "Loaded · " + str(loaded)
        elif configured:
            service = "Offline · set to " + str(configured)
        else:
            service = "Offline · no model selected"
        rows = [("Planner service", service),
                ("Refresh models", "Scan downloaded GGUFs")]
        for model in info.get("models", []):
            if not model["supported"]:
                detail = "Too large for safe memory limit"
            elif model["id"] == loaded:
                detail = "Loaded now"
            elif model["id"] == configured:
                detail = "Selected · service stopped"
            else:
                detail = "A to load this model"
            rows.append((model["id"], detail))
        self.rows("Local AI", rows,
                  "On-device planner · %d model(s) found" % len(info.get("models", [])))

    def begin_wifi_entry(self, ssid, hidden=False):
        self.wifi_ssid, self.wifi_hidden = ssid, hidden
        self.edit = {"title": "Network name" if hidden else "Wi-Fi password",
                     "text": ssid if hidden else "", "kind": "ssid" if hidden else "password",
                     "upper": False, "symbols": False, "cursor": [0, 0]}

    def use_keyboard_key(self, key):
        e = self.edit
        if key == "DONE":
            if e["kind"] == "token":
                token = e["text"].strip()
                if not re.fullmatch(r"mgst_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]", token):
                    self.say("That does not look like a Muse SDK token")
                    return
                e["text"] = ""
                self.edit = None
                self.say("Saving token in root-only storage…", 15)
                self.start(store_muse_token, token)
                return
            if e["kind"] == "ssid":
                if not e["text"].strip():
                    self.say("Enter a network name first")
                    return
                self.wifi_ssid = e["text"].strip()
                self.edit = {"title": "Wi-Fi password", "text": "", "kind": "password",
                             "upper": False, "symbols": False, "cursor": [0, 0]}
                return
            if not e["text"]:
                self.say("Enter the Wi-Fi password first")
                return
            self.wifi_password = e["text"]
            self.edit = None
            self.say("Connecting…", 35)
            self.start(wifi_connect, self.wifi_ssid, self.wifi_password, self.wifi_hidden)
            self.wifi_password = ""
        elif key == "CANCEL":
            if e["kind"] == "token":
                e["text"] = ""
            self.edit = None
        elif key == "DEL":
            e["text"] = e["text"][:-1]
        elif key == "CLEAR":
            e["text"] = ""
        elif key == "SPACE":
            e["text"] += " "
        elif key == "SHIFT":
            e["upper"] = not e["upper"]
        elif key == "SYM":
            e["symbols"] = not e["symbols"]
        elif key == "SHOW":
            e["show"] = not e.get("show", False)
        elif key:
            limit = 32 if e["kind"] == "ssid" else (128 if e["kind"] == "token" else 63)
            if len(e["text"]) < limit:
                e["text"] += key.upper() if e["upper"] else key

    def draw_keyboard(self):
        self.screen.fill((8, 11, 20))
        panel = pygame.Rect(12, 12, W - 24, H - 24)
        pygame.draw.rect(self.screen, PANEL, panel, border_radius=16)
        self.screen.blit(self.item.render(self.edit["title"], True, ACCENT), (26, 23))
        value = self.edit["text"]
        shown = value if self.edit.get("show") or self.edit["kind"] == "ssid" else "•" * len(value)
        if not shown:
            shown = "Type here with the D-pad keyboard"
        pygame.draw.rect(self.screen, BG, (24, 51, W - 48, 43), border_radius=7)
        self.screen.blit(self.small.render(shown[-52:], True, WHITE if value else MUTED), (34, 64))
        if self.edit["symbols"]:
            keyrows = [list("1234567890"), list("@#$%&*-_+"), list("!?:;.,/\\"), list("()[]{}'\"=")]
        else:
            keyrows = [list("1234567890"), list("qwertyuiop"), list("asdfghjkl"), list("zxcvbnm@._-")]
        x0, y0, cw, ch, gap = 23, 108, 55, 38, 4
        cx, cy = self.edit["cursor"]
        for r, keys in enumerate(keyrows):
            for c, key in enumerate(keys):
                x, y = x0 + c * (cw + gap), y0 + r * (ch + 5)
                rect = pygame.Rect(x, y, cw, ch)
                active = (r, c) == (cy, cx)
                pygame.draw.rect(self.screen, PANEL_HI if active else (31, 40, 58), rect, border_radius=6)
                if active:
                    pygame.draw.rect(self.screen, ACCENT, rect, 2, border_radius=6)
                label = key.upper() if self.edit["upper"] else key
                surf = self.item.render(label, True, WHITE)
                self.screen.blit(surf, surf.get_rect(center=rect.center))
        controls = self._control_keys()
        bx, by, bw, bh, bgap = 18, 293, 73, 40, 4
        for c, key in enumerate(controls):
            if not key:
                continue
            rect = pygame.Rect(bx + c * (bw + bgap), by, bw, bh)
            active = (cy == 4 and cx == c)
            pygame.draw.rect(self.screen, PANEL_HI if active else (31, 40, 58), rect, border_radius=6)
            if active:
                pygame.draw.rect(self.screen, ACCENT, rect, 2, border_radius=6)
            surf = self.tiny.render(key, True, WHITE)
            self.screen.blit(surf, surf.get_rect(center=rect.center))
        self.screen.blit(self.tiny.render("Arrows move · A type · X shift · B cancel · Select home", True, MUTED), (23, 354))
        help_text = ("Token stays hidden; it is stored only on this device." if self.edit["kind"] == "token"
                     else "Password stays hidden. Done connects and saves this network.")
        self.screen.blit(self.tiny.render(help_text, True, MUTED), (23, 374))

    def keyboard_move(self, key):
        e = self.edit
        c, r = e["cursor"]
        if key == pygame.K_LEFT:
            c = max(0, c - 1)
            while c > 0 and not self._keyrow(r)[c]:
                c -= 1
        elif key == pygame.K_RIGHT:
            c = min(len(self._keyrow(r)) - 1, c + 1)
            while c < len(self._keyrow(r)) - 1 and not self._keyrow(r)[c]:
                c += 1
        elif key == pygame.K_UP:
            r = max(0, r - 1)
            c = self._nearest_key_column(r, c)
        elif key == pygame.K_DOWN:
            r = min(4, r + 1)
            c = self._nearest_key_column(r, c)
        elif key == pygame.K_TAB:
            e["upper"] = not e["upper"]
        elif key == pygame.K_BACKSPACE:
            e["text"] = e["text"][:-1]
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            selected = self._keyrow(r)[c]
            if selected:
                self.use_keyboard_key(selected)
        elif key == pygame.K_ESCAPE:
            self.edit = None
        e["cursor"] = [c, r]

    def _keyrow(self, row):
        if row == 4:
            return self._control_keys()
        if self.edit["symbols"]:
            return [list("1234567890"), list("@#$%&*-_+"), list("!?:;.,/\\"), list("()[]{}'\"=")][row]
        return [list("1234567890"), list("qwertyuiop"), list("asdfghjkl"), list("zxcvbnm@._-")][row]

    def _control_keys(self):
        show = "SHOW" if self.edit["kind"] in ("password", "token") else ""
        return ["SHIFT", "SYM", "SPACE", "DEL", "CLEAR", show, "CANCEL", "DONE"]

    def _nearest_key_column(self, row, column):
        keys = self._keyrow(row)
        column = min(max(0, column), len(keys) - 1)
        if keys[column]:
            return column
        return min((i for i, value in enumerate(keys) if value),
                   key=lambda i: (abs(i - column), i))

    def wifi_select(self):
        if self.row == 0:
            state = "off" if self.wifi_radio else "on"
            self.say("Turning Wi-Fi " + state + "…")
            self.start(wifi_radio, state)
        elif self.row == 1:
            if not self.wifi_radio:
                self.say("Turn Wi-Fi on first", 8)
                return
            self.say("Scanning nearby networks…", 20)
            self.start(wifi_scan)
        elif self.row == 2:
            self.begin_wifi_entry("", hidden=True)
        elif self.wifi_connectivity == "portal" and self.row == 4:
            self.start(launch_wifi_portal)
        else:
            ap_offset = 5 if self.wifi_connectivity == "portal" else 4
            if self.row < ap_offset:
                return
            ap = self.wifi_aps[self.row - ap_offset]
            self.wifi_ssid = ap["ssid"]
            self.wifi_hidden = False
            if ap["connected"]:
                self.say("Already connected to " + ap["ssid"])
            elif ap.get("known"):
                self.say("Connecting to saved network…", 35)
                self.start(wifi_connect, ap["ssid"], None, False)
            elif not ap["security"]:
                self.say("Connecting to " + ap["ssid"] + "…", 35)
                self.start(wifi_connect, ap["ssid"], None, False)
            else:
                self.begin_wifi_entry(ap["ssid"])

    def choose(self):
        if self.page == "main":
            self.open_page(["wifi", "bluetooth", "audio", "devices",
                            "local_ai", "system"][self.sel])
        elif self.page == "wifi":
            self.wifi_select()
        elif self.page == "bluetooth":
            if self.row == 0:
                action = "power off" if self.bt_powered else "power on"
                self.start(bluetooth_action, action)
            elif self.row == 1:
                self.say("Scanning… make your accessory discoverable", 15)
                self.start(bluetooth_devices, True)
            elif self.row == 2:
                self.edit = {"title": "Muse SDK token (not BLE pairing)", "text": "", "kind": "token",
                             "upper": False, "symbols": False, "cursor": [0, 0]}
            elif self.row == 3:
                if not self.muse_token_configured:
                    self.say("Enter your personal SDK token first", 8)
                else:
                    self.say("Opening Muse pairing…", 15)
                    self.start(start_muse_pairing)
            else:
                d = self.bt_devices[self.row - 4]
                self.start(bluetooth_action, "disconnect" if d["connected"] else ("connect" if d["paired"] else "pair"), d["mac"])
                self.say("Connecting…", 25)
        elif self.page == "audio":
            sinks, sources = self.audio.get("sinks", []), self.audio.get("sources", [])
            mic_row = len(sinks) + len(sources) + 4
            if self.row == mic_row:
                _, _, running = self.mic_meter.snapshot()
                if running:
                    self.mic_meter.stop()
                    self.say("Mic check stopped; no recording was saved")
                else:
                    source = self.audio.get("defaults", {}).get("source")
                    if self.mic_meter.start(source):
                        self.say("Speak now; live mic level appears here", 30)
                    else:
                        self.say(self.mic_meter.error or "Select a microphone first", 8)
                return
            if 1 <= self.row <= len(sinks):
                dev = sinks[self.row - 1]
                rc, _ = command(["pactl", "set-default-sink", dev["name"]])
                self.say("Speaker set to " + dev["label"] if rc == 0 else "Could not select speaker")
                self.start(audio_snapshot)
            elif len(sinks) + 2 <= self.row < len(sinks) + 2 + len(sources):
                dev = sources[self.row - len(sinks) - 2]
                self.mic_meter.stop()
                rc, _ = command(["pactl", "set-default-source", dev["name"]])
                self.say("Microphone set to " + dev["label"] if rc == 0 else "Could not select microphone")
                self.start(audio_snapshot)
        elif self.page == "local_ai":
            if self.row == 0:
                loaded = self.local_ai.get("loaded")
                self.say("Loaded " + str(loaded) if loaded else
                         "Planner service is not running")
            elif self.row == 1:
                self.say("Refreshing local model list…", 20)
                self.start(local_model_snapshot)
            else:
                model_index = self.row - 2
                models = self.local_ai.get("models", [])
                if model_index >= len(models):
                    self.say("Model list changed; refresh and try again")
                    return
                model = models[model_index]
                if not model["supported"]:
                    self.say("This model exceeds the safe memory limit")
                elif model["id"] == self.local_ai.get("loaded"):
                    self.say("That model is already loaded")
                else:
                    self.say("Loading " + model["id"][:28] + "…", 240)
                    self.start(select_local_model, model["id"])
        elif self.page in ("devices", "system"):
            return

    def back(self):
        if self.edit:
            self.edit = None
        elif self.page == "main":
            self.page = "exit"
        else:
            if self.page == "audio":
                self.mic_meter.stop()
            self.page = "main"
            self.row = 0

    def handle_key(self, key):
        if self.edit:
            self.keyboard_move(key)
            return
        if key == pygame.K_ESCAPE:
            self.back()
            return
        if self.page == "main":
            if key in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT):
                dx = -1 if key == pygame.K_LEFT else (1 if key == pygame.K_RIGHT else 0)
                dy = -2 if key == pygame.K_UP else (2 if key == pygame.K_DOWN else 0)
                self.sel = (self.sel + dx + dy) % len(self.menu)
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.choose()
            return
        if key in (pygame.K_UP, pygame.K_DOWN):
            count = self.row_count()
            self.row = (self.row + (-1 if key == pygame.K_UP else 1)) % max(count, 1)
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.choose()
        elif self.page == "audio" and key in (pygame.K_LEFT, pygame.K_RIGHT):
            self.adjust_volume(-5 if key == pygame.K_LEFT else 5)

    def row_count(self):
        if self.page == "wifi":
            return 4 + int(self.wifi_connectivity == "portal") + len(self.wifi_aps)
        if self.page == "bluetooth":
            return 4 + len(self.bt_devices)
        if self.page == "audio":
            return 5 + len(self.audio.get("sinks", [])) + len(self.audio.get("sources", []))
        if self.page == "devices":
            return max(1, len(self.devices["usb"]) + len(self.devices["cameras"]))
        if self.page == "system":
            return 6
        if self.page == "local_ai":
            return 2 + len(self.local_ai.get("models", []))
        return 1

    def adjust_volume(self, change):
        sinks, sources = self.audio.get("sinks", []), self.audio.get("sources", [])
        out_row = len(sinks) + len(sources) + 2
        in_row = out_row + 1
        if self.row == out_row:
            self.volume_out = max(0, min(100, self.volume_out + change))
            command(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%d%%" % self.volume_out], timeout=3)
        elif self.row == in_row:
            self.volume_in = max(0, min(100, self.volume_in + change))
            command(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", "%d%%" % self.volume_in], timeout=3)

    def draw(self):
        self.poll_worker()
        if self.edit:
            self.draw_keyboard()
        elif self.page == "main":
            self.draw_main()
        elif self.page == "wifi":
            self.draw_wifi()
        elif self.page == "bluetooth":
            self.draw_bluetooth()
        elif self.page == "audio":
            self.draw_audio()
        elif self.page == "devices":
            self.draw_devices()
        elif self.page == "system":
            self.draw_system()
        elif self.page == "local_ai":
            self.draw_local_ai()
        pygame.display.flip()

    def run(self):
        clock = pygame.time.Clock()
        held_directions = set()
        try:
            while self.page != "exit":
                for ev in pygame.event.get():
                    if ev.type == pygame.QUIT:
                        return
                    if ev.type == pygame.KEYUP:
                        held_directions.discard(ev.key)
                    if ev.type == pygame.KEYDOWN:
                        # SDL can repeat KEYDOWN while a gamepad axis is held.
                        # Treat one physical press as one cursor step.
                        if not accept_direction_press(ev.key, held_directions):
                            continue
                        self.handle_key(ev.key)
                self.draw()
                clock.tick(30)
        finally:
            self.mic_meter.stop()


def wifi_radio(state):
    rc, out = command(["nmcli", "radio", "wifi", state], timeout=8)
    state_rc, current = command(["nmcli", "radio", "wifi"], timeout=5)
    actual = current.strip() if state_rc == 0 else "unknown"
    detail = " ".join(out.split())[:70]
    return {"ok": rc == 0, "radio": actual,
            "message": "Wi-Fi " + state if rc == 0 else
            "Could not turn Wi-Fi " + state + (": " + detail if detail else "")}


def system_snapshot():
    data = {"host": "GPi CM4"}
    _, out = command(["hostname"])
    data["host"] = out or "GPi CM4"
    _, out = command(["nmcli", "-g", "GENERAL.STATE", "device", "show", "wlan0"])
    data["wifi"] = "Connected" if out.startswith("100") else "Not connected"
    _, out = command(["hostname", "-I"])
    data["ip"] = out.split()[0] if out else "No address"
    try:
        raw = open("/sys/class/thermal/thermal_zone0/temp").read().strip()
        data["temp"] = "%.1f °C" % (int(raw) / 1000.0)
    except Exception:
        data["temp"] = "Unavailable"
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                data["memory"] = "%.1f GB available" % (int(line.split()[1]) / 1024 / 1024)
                break
    except Exception:
        pass
    batteries = __import__("glob").glob("/sys/class/power_supply/*/capacity")
    if batteries:
        try:
            percent = open(batteries[0]).read().strip()
            data["battery"] = percent + "% charge"
        except OSError:
            data["battery"] = "Charge level unavailable"
    else:
        # RetroFlag lists a 4,000 mAh cell, but this GPi currently exposes no
        # power_supply capacity node for Linux to read.
        data["battery"] = "4,000 mAh · charge level unavailable"
    return data


if __name__ == "__main__":
    SettingsApp().run()
