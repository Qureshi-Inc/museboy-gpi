#!/usr/bin/env python3
"""Render README previews from the app's real pygame draw routines.

This runs on a desktop with pygame-ce and requests installed. The dummy SDL
video driver renders at the GPi's 640x480 UI resolution without hardware.
"""
import importlib.util
import json
import os
import sys
import tempfile
import types

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "common"))

# The screenshots render app code without a Linux input device or GPi hardware.
ui = types.ModuleType("gpi_ui")
ui.W, ui.H = 640, 480
ui.BLACK, ui.WHITE = (8, 8, 12), (235, 235, 240)
ui.ACCENT, ui.DIM, ui.DARK = (255, 176, 64), (130, 130, 142), (24, 24, 32)
ui.GlobalKeyWatcher = type("GlobalKeyWatcher", (), {"__init__": lambda self, callback: None})
sys.modules["gpi_ui"] = ui
evdev = types.ModuleType("evdev")
evdev.ecodes = types.SimpleNamespace(KEY_F13=183)
sys.modules["evdev"] = evdev

import pygame

pygame.init()
_set_mode = pygame.display.set_mode
pygame.display.set_mode = lambda size, flags=0, *args, **kwargs: _set_mode(
    size, 0, *args, **kwargs)
pygame.display.set_mode((640, 480))
pygame.mouse.set_visible(False)
OUT = os.path.join(ROOT, "docs", "images")
os.makedirs(OUT, exist_ok=True)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(surface, name):
    pygame.image.save(surface, os.path.join(OUT, name))


launcher = load("museboy_launcher", os.path.join(ROOT, "launcher", "launcher.py"))
home = launcher.Launcher()
apps = []
for app_dir in ("appmart", "builder", "settings"):
    path = os.path.join(ROOT, "apps", app_dir)
    with open(os.path.join(path, "app.json"), encoding="utf-8") as f:
        app = json.load(f)
    app["_dir"] = path
    apps.append(app)
home.draw_grid(apps, 0)
save(home.screen, "home.png")

appmart = load("museboy_appmart", os.path.join(ROOT, "apps", "appmart", "main.py"))
shop = appmart.Appmart()
shop.draw_shelf(0)
pygame.display.flip()
save(shop.screen, "app-mart.png")

builder = load("museboy_builder", os.path.join(ROOT, "apps", "builder", "builder.py"))
builder.REQ_DIR = tempfile.mkdtemp(prefix="museboy-preview-requests-")
builder.BUILD_DIR = tempfile.mkdtemp(prefix="museboy-preview-builds-")
builder.TMP_DIR = tempfile.mkdtemp(prefix="museboy-preview-tmp-")
maker = builder.BuilderApp()
maker.current_plan = {
    "title": "Pocket Field Guide", "goal": "Explore a living world with Muse.",
    "user_flow": ["Choose a region", "Ask Muse what changed"],
    "screens": [{"name": "World", "purpose": "Explore", "states": {
        "normal": "A path and nearby discoveries are shown.", "empty": "No discoveries yet.",
        "loading": "Loading the region.", "error": "Keep exploring offline."},
        "controls": {"up": "Move north", "down": "Move south", "left": "Turn left",
                     "right": "Turn right", "A": "Inspect", "B": "Back", "X": "Ask Muse",
                     "Y": "Open tools", "Start": "Pause"}}],
    "data_sources": [{"name": "Muse-generated region", "endpoint": "Muse gadget task",
        "params": "approved text plan", "timeout_seconds": 30,
        "network_failure_behavior": "Show cached region."}],
    "input_constraints": "D-pad and A/B/X/Y/Start only.",
    "icon_art_direction": "A tiny moonlit trail marker.", "out_of_scope": [],
    "acceptance_checks": ["The user can walk north with Up.",
                           "The user can inspect a nearby discovery with A.",
                           "The user can return with B."], "open_questions": []}
maker.state = "PREVIEW"
maker.draw()
save(maker.screen, "app-builder.png")

settings = load("museboy_settings", os.path.join(ROOT, "apps", "settings", "main.py"))
control = settings.SettingsApp()
control.wifi_aps = [{"ssid": "Home Wi-Fi", "connected": True}]
control.sel = 4
control.local_ai = {"models": [{"id": "gemma-3-1b-it-Q4_K_M", "supported": True}],
                    "loaded": "gemma-3-1b-it-Q4_K_M",
                    "configured": "gemma-3-1b-it-Q4_K_M", "running": True}
control.draw()
save(control.screen, "settings.png")
control.page = "local_ai"
control.row = 2
control.draw()
save(control.screen, "local-ai.png")

pygame.quit()
