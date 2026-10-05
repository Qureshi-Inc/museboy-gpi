#!/usr/bin/env python3
"""Appmart - the app shop for the GPi handheld. Slot in something new."""
import io
import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import zipfile

import pygame
import requests

sys.path.insert(0, "/opt/gpi/common")
from gpi_ui import W, H, BLACK, WHITE, DIM, DARK  # noqa: E402
from bolt import Bolt  # noqa: E402


def draw_keycap(surf, font, x, y, key, color):
    """Draw a small rounded keycap with the key label; return its width."""
    label = font.render(key, True, color)
    w = label.get_width() + 16
    h = label.get_height() + 10
    rect = pygame.Rect(x, y, w, h)
    pygame.draw.rect(surf, (40, 40, 52), rect, border_radius=6)
    pygame.draw.rect(surf, color, rect, 1, border_radius=6)
    surf.blit(label, (x + 8, y + 5))
    return w

APPS_DIR = "/opt/gpi/apps"
SHOP_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.join(SHOP_DIR, "demo")

def load_shop_config():
    """Connect every MuseBoy install to the shared community App Mart."""
    token = ""
    for config_path in (os.environ.get("GPI_APPMART_CONFIG"), "/etc/gpi/appmart.json"):
        if not config_path:
            continue
        try:
            with open(config_path, encoding="utf-8") as f:
                token = json.load(f).get("submit_token", "")
            break
        except (OSError, ValueError, AttributeError):
            pass
    return "https://museboy-app-mart.rodeomasjid.workers.dev", token


# Each owner can connect their own catalog at setup time without editing the app.
SHOP_URL, SUBMIT_TOKEN = load_shop_config()

# Warm shop palette
BG_TOP = (24, 18, 12)
BG_BOT = (48, 33, 20)
WOOD = (107, 74, 47)
WOOD_DARK = (74, 50, 30)
AMBER = (255, 176, 32)
AMBER_DIM = (160, 110, 40)
CREAM = (245, 230, 205)
BRASS = (190, 150, 90)


def rounded(surf, rect, color, radius=14, width=0):
    pygame.draw.rect(surf, color, rect, width, border_radius=radius)


def fetch_shelf(query="", category=""):
    """Return list of app dicts from the backend, or None if unreachable."""
    if not SHOP_URL:
        return None
    try:
        r = requests.get(SHOP_URL.rstrip("/") + "/api/apps",
                         params={"q": query, "category": "" if category == "all" else category},
                         timeout=6)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def fetch_icon(app_id):
    if not SHOP_URL:
        return None
    try:
        r = requests.get(f"{SHOP_URL.rstrip('/')}/api/apps/{app_id}/icon", timeout=6)
        if r.status_code == 200 and r.content[:4] == b"\x89PNG":
            return pygame.image.load(io.BytesIO(r.content)).convert_alpha()
    except Exception:
        pass
    return None


def fetch_categories():
    if not SHOP_URL:
        return ["all"]
    try:
        r = requests.get(SHOP_URL.rstrip("/") + "/api/categories", timeout=6)
        if r.status_code == 200:
            return ["all"] + [row["category"] for row in r.json() if row.get("category")]
    except Exception:
        pass
    return ["all"]


def load_icon(app, size):
    """Load an app icon at given size, with procedural fallback."""
    img = None
    if app.get("demo"):
        p = os.path.join(DEMO_DIR, app["id"], "icon.png")
        if os.path.exists(p):
            try:
                img = pygame.image.load(p).convert_alpha()
            except Exception:
                img = None
    else:
        img = fetch_icon(app["id"])
    if img is None:
        img = procedural_icon(app.get("name", "?"), size)
    return pygame.transform.smoothscale(img, (size, size))


def procedural_icon(name, size):
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    hue_i = sum(ord(c) for c in name) % 6
    cols = [(58, 134, 255), (255, 110, 110), (80, 200, 120),
            (255, 176, 32), (170, 110, 255), (60, 200, 200)]
    c = cols[hue_i]
    pygame.draw.rect(surf, c, (0, 0, size, size), border_radius=size // 6)
    pygame.draw.rect(surf, tuple(max(0, x - 40) for x in c),
                     (0, 0, size, size), 3, border_radius=size // 6)
    f = pygame.font.SysFont("dejavusans", int(size * 0.55), bold=True)
    t = f.render(name[:1].upper(), True, WHITE)
    surf.blit(t, t.get_rect(center=(size // 2, size // 2)))
    return surf


def speech_bubble(s, font, text, x, y, maxw=250):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = cur + " " + w_ if cur else w_
        if font.size(t)[0] > maxw - 24:
            lines.append(cur)
            cur = w_
        else:
            cur = t
    if cur:
        lines.append(cur)
    lh = font.get_linesize()
    bw, bh = maxw, lh * len(lines) + 20
    pygame.draw.rect(s, WHITE, (x, y, bw, bh), border_radius=12)
    pygame.draw.polygon(s, WHITE, [(x + 30, y + bh - 2), (x + 14, y + bh + 14),
                                   (x + 52, y + bh - 2)])
    for i, ln in enumerate(lines):
        s.blit(font.render(ln, True, BLACK), (x + 12, y + 10 + i * lh))


def demo_apps():
    apps = []
    if os.path.isdir(DEMO_DIR):
        for app_id in sorted(os.listdir(DEMO_DIR)):
            meta = os.path.join(DEMO_DIR, app_id, "app.json")
            if os.path.exists(meta):
                try:
                    with open(meta) as f:
                        m = json.load(f)
                    m["demo"] = True
                    apps.append(m)
                except Exception:
                    pass
    return apps


def install_app(app, status_cb=None):
    """Validate into a staging directory, then atomically install an app."""
    app_id = app.get("id", "")
    if not isinstance(app_id, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?", app_id):
        return False, "Install failed: invalid app ID"
    if app_id in {"appmart", "builder", "settings"}:
        return False, "Install failed: app ID is reserved"
    dest = os.path.join(APPS_DIR, app_id)
    staging = None
    backup = None
    try:
        os.makedirs(APPS_DIR, exist_ok=True)
        if os.path.islink(dest):
            raise ValueError("installed app path is a symlink")
        staging = tempfile.mkdtemp(prefix=".appmart-install-", dir=APPS_DIR)
        if app.get("demo"):
            src = os.path.join(DEMO_DIR, app_id)
            if not os.path.isdir(src):
                raise ValueError("demo package is missing")
            for fn in os.listdir(src):
                s, d = os.path.join(src, fn), os.path.join(dest, fn)
                if os.path.islink(s):
                    raise ValueError("demo package contains a symlink")
                if os.path.isfile(s):
                    shutil.copy2(s, os.path.join(staging, fn))
        else:
            r = requests.get(f"{SHOP_URL.rstrip('/')}/api/apps/{app_id}/download",
                             timeout=30)
            r.raise_for_status()
            if len(r.content) > 10 * 1024 * 1024:
                raise ValueError("app package exceeds the 10 MB download limit")
            expected_hash = r.headers.get("x-app-sha256", "")
            if expected_hash and hashlib.sha256(r.content).hexdigest() != expected_hash:
                raise ValueError("package checksum does not match the catalog")
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            infos = zf.infolist()
            if not infos or len(infos) > 256:
                raise ValueError("package has an invalid file count")
            expanded = 0
            for info in infos:
                name = info.filename
                normalized = posixpath.normpath(name)
                mode = info.external_attr >> 16
                if (name.startswith(("/", "\\")) or "\\" in name or
                        ":" in name or normalized in ("", ".", "..") or
                        normalized.startswith("../") or stat.S_ISLNK(mode)):
                    raise ValueError("package contains an unsafe path or symlink")
                expanded += info.file_size
                if info.file_size > 10 * 1024 * 1024 or expanded > 32 * 1024 * 1024:
                    raise ValueError("package expands beyond the install size limit")
            zf.extractall(staging)
        meta = os.path.join(staging, "app.json")
        if not os.path.exists(meta):
            raise ValueError("bundle missing app.json")
        with open(meta, encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("id") != app_id:
            raise ValueError("manifest app ID does not match the catalog")
        run_target = manifest.get("exec")
        if not isinstance(run_target, str) or not run_target:
            raise ValueError("manifest is missing its launch command")
        if os.path.isabs(run_target):
            if os.path.commonpath((os.path.abspath(dest), os.path.abspath(run_target))) != os.path.abspath(dest):
                raise ValueError("launch command must point inside this app")
            executable = os.path.join(staging, os.path.relpath(run_target, dest))
        else:
            if ".." in run_target.replace("\\", "/").split("/"):
                raise ValueError("launch command contains an unsafe path")
            executable = os.path.join(staging, run_target)
        executable = os.path.realpath(executable)
        if os.path.commonpath((os.path.realpath(staging), executable)) != os.path.realpath(staging):
            raise ValueError("launch command escapes the app directory")
        if not os.path.isfile(executable):
            raise ValueError("launch command points to a missing file")
        os.chmod(executable, os.stat(executable).st_mode | 0o111)
        manifest["exec"] = os.path.join(dest, os.path.relpath(executable, staging))
        with open(meta, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False)
        if os.path.isdir(dest):
            backup_root = os.path.join(APPS_DIR, ".appmart-backups")
            os.makedirs(backup_root, exist_ok=True)
            backup = os.path.join(backup_root, "%s-%d" % (app_id, int(time.time())))
            if os.path.exists(backup):
                backup += "-%d" % os.getpid()
            os.replace(dest, backup)
        try:
            os.replace(staging, dest)
            staging = None
        except Exception:
            if backup and os.path.isdir(backup) and not os.path.exists(dest):
                os.replace(backup, dest)
            raise
        return True, "Installed!" if not backup else "Updated! Previous copy kept as a backup."
    except Exception as e:
        return False, f"Install failed: {e}"
    finally:
        if staging and os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)


def installed_apps():
    """Read valid, non-system apps which can be shared from this GPi."""
    result = []
    if not os.path.isdir(APPS_DIR):
        return result
    for app_id in sorted(os.listdir(APPS_DIR)):
        if app_id in {"appmart", "builder", "settings"} or app_id.startswith("."):
            continue
        directory = os.path.join(APPS_DIR, app_id)
        if not os.path.isdir(directory) or os.path.islink(directory):
            continue
        try:
            with open(os.path.join(directory, "app.json"), encoding="utf-8") as f:
                app = json.load(f)
            if app.get("id") == app_id and isinstance(app.get("exec"), str):
                app["_path"] = directory
                result.append(app)
        except (OSError, ValueError, AttributeError):
            continue
    return result


def submit_app(app):
    """Package one installed app and submit it privately for marketplace review."""
    if not SHOP_URL or not SUBMIT_TOKEN:
        raise ValueError("Browsing works without a key. Ask the App Mart operator for contributor access to submit apps.")
    app_dir = app["_path"]
    manifest = {key: value for key, value in app.items() if not key.startswith("_")}
    executable = manifest.get("exec", "")
    if os.path.isabs(executable):
        if os.path.commonpath((os.path.realpath(app_dir), os.path.realpath(executable))) != os.path.realpath(app_dir):
            raise ValueError("App launch file must be inside its app folder")
        manifest["exec"] = os.path.relpath(executable, app_dir)
    elif ".." in executable.replace("\\", "/").split("/"):
        raise ValueError("App launch file has an unsafe path")
    executable_path = os.path.realpath(os.path.join(app_dir, manifest["exec"]))
    if os.path.commonpath((os.path.realpath(app_dir), executable_path)) != os.path.realpath(app_dir) or not os.path.isfile(executable_path):
        raise ValueError("App launch file is missing or outside its app folder")
    temp_dir = tempfile.mkdtemp(prefix="appmart-share-")
    try:
        zip_path = os.path.join(temp_dir, "app.zip")
        total = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("app.json", json.dumps(manifest, ensure_ascii=False))
            for root, dirs, files in os.walk(app_dir, followlinks=False):
                dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
                for filename in files:
                    full = os.path.join(root, filename)
                    if os.path.islink(full) or filename.endswith((".pyc", ".pyo")):
                        continue
                    arcname = os.path.relpath(full, app_dir).replace(os.sep, "/")
                    if arcname == "app.json":
                        continue
                    total += os.path.getsize(full)
                    if total > 9 * 1024 * 1024:
                        raise ValueError("App files exceed the 9 MB sharing limit")
                    archive.write(full, arcname)
        icon_path = os.path.join(app_dir, "icon.png")
        if not os.path.isfile(icon_path):
            icon_path = os.path.join(temp_dir, "icon.png")
            pygame.image.save(procedural_icon(app.get("name", "?"), 128), icon_path)
        files = {
            "manifest": (None, json.dumps(manifest, ensure_ascii=False), "application/json"),
            "bundle": (f"{manifest['id']}.zip", open(zip_path, "rb"), "application/zip"),
            "icon": ("icon.png", open(icon_path, "rb"), "image/png"),
        }
        try:
            response = requests.post(
                SHOP_URL.rstrip("/") + "/api/submissions",
                headers={"Authorization": f"Bearer {SUBMIT_TOKEN}"},
                files=files, timeout=60)
        finally:
            for part in files.values():
                if part[1] is not None and hasattr(part[1], "close"):
                    part[1].close()
        if response.status_code != 202:
            try:
                message = response.json().get("error", "Submission failed")
            except Exception:
                message = "Submission failed"
            raise ValueError(message)
        return "Sent for review. It appears in App Mart after approval."
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class Appmart:
    def __init__(self):
        pygame.init()
        pygame.mouse.set_visible(False)
        pygame.display.set_caption("Appmart")
        self.screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
        self.clock = pygame.time.Clock()
        self.f_title = pygame.font.SysFont("dejavusans", 30, bold=True)
        self.f_name = pygame.font.SysFont("dejavusans", 20, bold=True)
        self.f_small = pygame.font.SysFont("dejavusans", 15)
        self.f_tiny = pygame.font.SysFont("dejavusans", 13)
        self.f_bubble = pygame.font.SysFont("dejavusans", 15)
        self.f_big = pygame.font.SysFont("dejavusans", 44, bold=True)
        self.bolt = Bolt()
        self.bolt_mood = "idle"

        self.state = "SHELF"
        self.search_query = ""
        self.categories = fetch_categories()
        self.category_index = 0
        self.shelf = fetch_shelf()
        self.demo = self.shelf is None
        if self.demo:
            self.shelf = demo_apps()
        self.sel = 0
        self.icon_cache = {}
        self.detail_app = None
        self.anim_t0 = 0
        self.installed_ok = False
        self.install_msg = ""
        self.flash = 0
        self.keyboard_row = 0
        self.keyboard_col = 0
        self.share_apps = []
        self.share_sel = 0
        self.share_thread = None
        self.share_result = ""

    def refresh_shelf(self):
        shelf = fetch_shelf(self.search_query, self.categories[self.category_index])
        if shelf is None:
            self.demo = True
            self.shelf = demo_apps()
            if self.search_query:
                self.shelf = [a for a in self.shelf if self.search_query.lower() in
                              (a.get("name", "") + " " + a.get("description", "")).lower()]
        else:
            self.demo = False
            self.shelf = shelf
        self.sel = min(self.sel, max(0, len(self.shelf) - 1))

    def icon(self, app, size):
        key = (app["id"], size)
        if key not in self.icon_cache:
            self.icon_cache[key] = load_icon(app, size)
        return self.icon_cache[key]

    # ---------- background ----------
    def draw_bg(self, t):
        for y in range(0, H, 4):
            k = y / H
            c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * k) for i in range(3))
            pygame.draw.line(self.screen, c, (0, y), (W, y))
        # faint shelf planks
        for y in (150, 300):
            pygame.draw.line(self.screen, (60, 44, 28), (0, y), (W, y), 2)

    def draw_header(self):
        t = self.f_title.render("Appmart", True, AMBER)
        self.screen.blit(t, (24, 14))
        s = self.f_small.render("the app shop", True, BRASS)
        self.screen.blit(s, (26 + t.get_width() + 10, 26))

    def draw_bolt(self, x, y, bubble=None):
        self.bolt.draw(self.screen, x, y, self.bolt_mood, 0.8)
        if bubble:
            speech_bubble(self.screen, self.f_bubble, bubble, x + 70, y - 10)

    def draw_footer(self, hints):
        y = H - 34
        x = 24
        for key, label in hints:
            w_ = draw_keycap(self.screen, self.f_tiny, x, y, key, WHITE)
            t = self.f_tiny.render(label, True, DIM)
            self.screen.blit(t, (x + w_ + 6, y + 2))
            x += w_ + 12 + self.f_tiny.size(label)[0] + 18

    # ---------- states ----------
    def draw_shelf(self, t):
        self.draw_bg(t)
        self.draw_header()
        if self.demo:
            bubble = "The shop opens soon! Here's a taste — on the house."
        elif not self.shelf:
            bubble = "Nothing stocked yet — go build one and share it!"
        else:
            bubble = "Fresh apps! Take a look around."
        self.draw_bolt(36, 120, bubble)
        filters = f"{self.categories[self.category_index].replace('-', ' ').title()}"
        if self.search_query:
            filters += f"  ·  “{self.search_query[:18]}”"
        ft = self.f_tiny.render(filters, True, BRASS)
        self.screen.blit(ft, (228, 66))

        # wooden shelf plank
        plank = pygame.Rect(210, 300, 410, 26)
        rounded(self.screen, plank, WOOD, 8)
        pygame.draw.line(self.screen, WOOD_DARK, (210, 322), (620, 322), 3)

        n = len(self.shelf)
        if n == 0:
            msg = self.f_small.render("The shelf is empty.", True, BRASS)
            self.screen.blit(msg, (330, 220))
        else:
            # show up to 3, scroll window around selection
            vis = 3
            start = max(0, min(self.sel - 1, n - vis))
            for i, app in enumerate(self.shelf[start:start + vis]):
                cx = 300 + i * 130
                selected = (start + i == self.sel)
                sz = 104 if selected else 78
                lift = 26 if selected else 0
                icon = self.icon(app, sz)
                ix = cx - sz // 2
                iy = 300 - sz + 8 - lift
                if selected:
                    pygame.draw.rect(self.screen, AMBER,
                                     (ix - 8, iy - 8, sz + 16, sz + 16),
                                     3, border_radius=14)
                    # shadow
                    sh = pygame.Surface((sz, 14), pygame.SRCALPHA)
                    pygame.draw.ellipse(sh, (0, 0, 0, 90), (0, 0, sz, 14))
                    self.screen.blit(sh, (ix, 306))
                self.screen.blit(icon, (ix, iy))
                nm = self.f_tiny.render(app.get("name", "?")[:14], True,
                                        CREAM if selected else BRASS)
                self.screen.blit(nm, nm.get_rect(center=(cx, 348)))
            if n > vis:
                dots = self.f_tiny.render(f"{self.sel + 1}/{n}", True, BRASS)
                self.screen.blit(dots, (600, 270))

        hints = [("A", "Pick it up"), ("X", "Search"), ("Y", "Category"), ("B", "Home")] if n else [("X", "Search"), ("Y", "Category"), ("B", "Home")]
        self.draw_footer(hints)

    def draw_search(self, t):
        self.draw_bg(t)
        self.draw_header()
        self.draw_bolt(36, 118, "Find an app on the shelf.")
        box = pygame.Rect(205, 93, 410, 52)
        rounded(self.screen, box, (52, 38, 24), 10)
        self.screen.blit(self.f_name.render(self.search_query or "Type to search…", True,
                            CREAM if self.search_query else BRASS), (box.x + 15, box.y + 14))
        rows = [list("abcdefghij"), list("klmnopqrst"), list("uvwxyz09-_")]
        for ri, row in enumerate(rows):
            for ci, char in enumerate(row):
                x, y = 220 + ci * 37, 175 + ri * 58
                rect = pygame.Rect(x, y, 31, 40)
                selected = ri == self.keyboard_row and ci == self.keyboard_col
                rounded(self.screen, rect, AMBER if selected else (52, 38, 24), 6)
                color = BLACK if selected else CREAM
                label = self.f_name.render(char, True, color)
                self.screen.blit(label, label.get_rect(center=rect.center))
        actions = [(220, "⌫"), (344, "Clear"), (472, "Search")]
        for ci, (x, label) in enumerate(actions):
            rect = pygame.Rect(x, 365, 110, 42)
            selected = self.keyboard_row == 3 and self.keyboard_col == ci
            rounded(self.screen, rect, AMBER if selected else (70, 54, 33), 8)
            txt = self.f_tiny.render(label, True, BLACK if selected else CREAM)
            self.screen.blit(txt, txt.get_rect(center=rect.center))
        self.draw_footer([("A", "Type / search"), ("Y", "Delete"), ("X", "Clear"), ("B", "Back")])

    def draw_my_apps(self, t):
        self.draw_bg(t)
        self.draw_header()
        self.draw_bolt(36, 120, "Pick one of your apps to share.")
        title = self.f_name.render("My installed apps", True, CREAM)
        self.screen.blit(title, (230, 82))
        if not self.share_apps:
            msg = self.f_small.render("No shareable apps found.", True, BRASS)
            self.screen.blit(msg, (250, 180))
        for i, app in enumerate(self.share_apps[:6]):
            y = 132 + i * 42
            rect = pygame.Rect(225, y, 370, 34)
            selected = i == self.share_sel
            rounded(self.screen, rect, AMBER if selected else (52, 38, 24), 7)
            label = self.f_small.render(app.get("name", app.get("id", "?"))[:28],
                                        True, BLACK if selected else CREAM)
            self.screen.blit(label, (rect.x + 12, rect.y + 7))
        self.draw_footer([("A", "Share for review"), ("B", "Back"), ("Start", "Home")])

    def draw_sharing(self, t):
        self.draw_bg(t)
        self.draw_header()
        self.draw_bolt(36, 120, "I’m sending it to the review shelf.")
        text = self.share_result or "Preparing your app…"
        words, lines, cur = text.split(), [], ""
        for word in words:
            candidate = cur + " " + word if cur else word
            if self.f_name.size(candidate)[0] > 340:
                lines.append(cur)
                cur = word
            else:
                cur = candidate
        if cur:
            lines.append(cur)
        for i, line in enumerate(lines):
            txt = self.f_name.render(line, True, CREAM)
            self.screen.blit(txt, txt.get_rect(center=(420, 240 + i * 30)))
        busy = self.share_thread is not None and self.share_thread.is_alive()
        self.draw_footer([] if busy else [("A", "My apps"), ("B", "Back")])

    def draw_detail(self, t):
        self.draw_bg(t)
        self.draw_header()
        app = self.detail_app
        self.draw_bolt(36, 120, "A fine choice! Slot it in?")
        # card
        card = pygame.Rect(210, 90, 410, 300)
        rounded(self.screen, card, (52, 38, 24), 18)
        rounded(self.screen, card, AMBER_DIM, 18, 2)
        icon = self.icon(app, 120)
        self.screen.blit(icon, (240, 130))
        tx = 390
        self.screen.blit(self.f_name.render(app.get("name", "?"), True, CREAM),
                         (tx, 125))
        desc = app.get("description", "")
        words, lines, cur = desc.split(), [], ""
        for w_ in words:
            tt = cur + " " + w_ if cur else w_
            if self.f_small.size(tt)[0] > 200:
                lines.append(cur)
                cur = w_
            else:
                cur = tt
        if cur:
            lines.append(cur)
        for i, ln in enumerate(lines[:3]):
            self.screen.blit(self.f_small.render(ln, True, DIM), (tx, 160 + i * 20))
        meta = f"by {app.get('author', 'unknown')}  ·  v{app.get('version', 1)}"
        self.screen.blit(self.f_tiny.render(meta, True, BRASS), (tx, 235))
        dl = f"⬇ {app.get('downloads', 0)} installs"
        self.screen.blit(self.f_tiny.render(dl, True, BRASS), (tx, 258))
        if app.get("demo"):
            tag = self.f_tiny.render("demo — free", True, AMBER)
            self.screen.blit(tag, (tx, 282))
        # slot-in button
        btn = pygame.Rect(240, 320, 350, 52)
        rounded(self.screen, btn, AMBER, 12)
        bt = self.f_name.render("Slot it in", True, BLACK)
        self.screen.blit(bt, bt.get_rect(center=btn.center))
        self.draw_footer([("A", "Slot it in"), ("B", "Back to shelf")])

    def draw_installing(self, t):
        self.draw_bg(t)
        self.draw_header()
        self.bolt_mood = "build"
        self.draw_bolt(36, 120)
        el = t - self.anim_t0
        app = self.detail_app
        # glowing slot at bottom
        slot = pygame.Rect(220, 400, 200, 54)
        glow = int(120 + 80 * math.sin(el * 6))
        rounded(self.screen, slot.inflate(12, 12), (glow, glow // 2, 20), 16)
        rounded(self.screen, slot, (30, 22, 14), 12)
        rounded(self.screen, slot, AMBER, 12, 2)
        st = self.f_small.render("SLOT", True, BRASS)
        self.screen.blit(st, st.get_rect(center=(320, 427)))

        if el < 0.7:
            # tile hovers at center
            k = el / 0.7
            y = 200
            sz = 110
        elif el < 1.7:
            # slides down into slot
            k = (el - 0.7) / 1.0
            k = k * k
            y = 200 + (400 - 200) * k
            sz = int(110 - 30 * k)
        else:
            y, sz = 400, 80
        icon = self.icon(app, max(sz, 40))
        self.screen.blit(icon, (320 - sz // 2, int(y - sz // 2)))
        if el >= 1.7 and self.flash <= 0:
            self.flash = 0.35
            # do the actual install once, at the click
            ok, msg = install_app(app)
            self.installed_ok, self.install_msg = ok, msg
        if self.flash > 0:
            self.flash -= 0.03
            a = max(0, int(200 * self.flash / 0.35))
            fl = pygame.Surface((W, H), pygame.SRCALPHA)
            fl.fill((255, 240, 200, a))
            self.screen.blit(fl, (0, 0))
            ring = pygame.Rect(0, 0, 120, 120)
            ring.center = (320, 427)
            pygame.draw.ellipse(self.screen, (255, 240, 200, a), ring, 4)
        if el > 2.3:
            self.state = "DONE"
            self.anim_t0 = t

    def draw_done(self, t):
        self.draw_bg(t)
        self.draw_header()
        self.bolt_mood = "happy"
        if self.installed_ok:
            self.draw_bolt(36, 120, "Click! It's on your home screen.")
        else:
            self.draw_bolt(36, 120, f"Hmm — {self.install_msg}")
        big = self.f_big.render("Installed!", True, AMBER) \
            if self.installed_ok else \
            self.f_big.render("Hmm.", True, (255, 120, 120))
        self.screen.blit(big, big.get_rect(center=(400, 220)))
        nm = self.f_name.render(self.detail_app.get("name", ""), True, CREAM)
        self.screen.blit(nm, nm.get_rect(center=(400, 270)))
        self.draw_footer([("A", "Keep browsing"), ("B", "Home")])

    # ---------- main loop ----------
    def run(self):
        running = True
        while running:
            t = time.time()
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == pygame.KEYDOWN:
                    k = e.key
                    if k in (pygame.K_ESCAPE,):
                        if self.state in ("DETAIL", "SEARCH", "MY_APPS"):
                            self.state = "SHELF"
                        elif self.state == "SHARING":
                            if not (self.share_thread and self.share_thread.is_alive()):
                                self.state = "MY_APPS"
                        else:
                            running = False
                    elif self.state == "SHELF":
                        n = len(self.shelf)
                        if k in (pygame.K_LEFT,):
                            self.sel = (self.sel - 1) % n if n else 0
                        elif k in (pygame.K_RIGHT,):
                            self.sel = (self.sel + 1) % n if n else 0
                        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            if n:
                                self.detail_app = self.shelf[self.sel]
                                self.bolt_mood = "think"
                                self.state = "DETAIL"
                        elif k == pygame.K_TAB:
                            self.state = "SEARCH"
                            self.keyboard_row = self.keyboard_col = 0
                        elif k == pygame.K_y and len(self.categories) > 1:
                            self.category_index = (self.category_index + 1) % len(self.categories)
                            self.refresh_shelf()
                        elif k == pygame.K_SPACE:
                            self.share_apps = installed_apps()
                            self.share_sel = 0
                            self.state = "MY_APPS"
                    elif self.state == "SEARCH":
                        rows = ["abcdefghij", "klmnopqrst", "uvwxyz09-_"]
                        if k == pygame.K_LEFT:
                            max_col = (len(rows[self.keyboard_row]) - 1
                                       if self.keyboard_row < 3 else 2)
                            self.keyboard_col = (self.keyboard_col - 1) % (max_col + 1)
                        elif k == pygame.K_RIGHT:
                            max_col = (len(rows[self.keyboard_row]) - 1
                                       if self.keyboard_row < 3 else 2)
                            self.keyboard_col = (self.keyboard_col + 1) % (max_col + 1)
                        elif k == pygame.K_UP:
                            self.keyboard_row = (self.keyboard_row - 1) % 4
                            self.keyboard_col = min(self.keyboard_col,
                                len(rows[self.keyboard_row]) - 1 if self.keyboard_row < 3 else 2)
                        elif k == pygame.K_DOWN:
                            self.keyboard_row = (self.keyboard_row + 1) % 4
                            self.keyboard_col = min(self.keyboard_col,
                                len(rows[self.keyboard_row]) - 1 if self.keyboard_row < 3 else 2)
                        elif k == pygame.K_y:
                            self.search_query = self.search_query[:-1]
                        elif k == pygame.K_TAB:
                            self.search_query = ""
                        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            if self.keyboard_row < 3:
                                char = rows[self.keyboard_row][self.keyboard_col]
                                if len(self.search_query) < 40:
                                    self.search_query += char
                            elif self.keyboard_col == 0:
                                self.search_query = self.search_query[:-1]
                            elif self.keyboard_col == 1:
                                self.search_query = ""
                            else:
                                self.refresh_shelf()
                                self.state = "SHELF"
                    elif self.state == "MY_APPS":
                        if k in (pygame.K_UP, pygame.K_LEFT) and self.share_apps:
                            self.share_sel = (self.share_sel - 1) % len(self.share_apps)
                        elif k in (pygame.K_DOWN, pygame.K_RIGHT) and self.share_apps:
                            self.share_sel = (self.share_sel + 1) % len(self.share_apps)
                        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.share_apps:
                            self.share_result = "Preparing and uploading…"
                            selected_app = self.share_apps[self.share_sel]
                            def upload(app=selected_app):
                                try:
                                    self.share_result = submit_app(app)
                                except Exception as exc:
                                    self.share_result = f"Could not share: {exc}"
                            self.share_thread = threading.Thread(target=upload, daemon=True)
                            self.share_thread.start()
                            self.state = "SHARING"
                        elif k == pygame.K_ESCAPE:
                            self.state = "SHELF"
                    elif self.state == "SHARING":
                        busy = self.share_thread is not None and self.share_thread.is_alive()
                        if not busy and k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            self.share_apps = installed_apps()
                            self.state = "MY_APPS"
                        elif not busy and k == pygame.K_ESCAPE:
                            self.state = "MY_APPS"
                    elif self.state == "DETAIL":
                        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            self.state = "INSTALLING"
                            self.anim_t0 = t
                            self.flash = 0
                            self.bolt_mood = "build"
                    elif self.state == "DONE":
                        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                            self.bolt_mood = "idle"
                            self.state = "SHELF"

            if self.state == "SHELF":
                self.draw_shelf(t)
            elif self.state == "DETAIL":
                self.draw_detail(t)
            elif self.state == "SEARCH":
                self.draw_search(t)
            elif self.state == "MY_APPS":
                self.draw_my_apps(t)
            elif self.state == "SHARING":
                self.draw_sharing(t)
            elif self.state == "INSTALLING":
                self.draw_installing(t)
            elif self.state == "DONE":
                self.draw_done(t)
            pygame.display.flip()
            self.clock.tick(30)


if __name__ == "__main__":
    Appmart().run()
