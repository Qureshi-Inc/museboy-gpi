#!/usr/bin/env python3
"""Appmart - the app shop for the GPi handheld. Slot in something new."""
import io
import json
import math
import os
import shutil
import sys
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

# Set this when the Appmart backend is live. Empty string = demo mode.
SHOP_URL = ""

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


def fetch_shelf():
    """Return list of app dicts from the backend, or None if unreachable."""
    if not SHOP_URL:
        return None
    try:
        r = requests.get(SHOP_URL.rstrip("/") + "/api/apps", timeout=6)
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
    """Install an app dict into APPS_DIR. Returns (ok, message)."""
    app_id = app["id"]
    dest = os.path.join(APPS_DIR, app_id)
    try:
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest, exist_ok=True)
        if app.get("demo"):
            src = os.path.join(DEMO_DIR, app_id)
            for fn in os.listdir(src):
                s, d = os.path.join(src, fn), os.path.join(dest, fn)
                if os.path.isfile(s):
                    shutil.copy2(s, d)
        else:
            r = requests.get(f"{SHOP_URL.rstrip('/')}/api/apps/{app_id}/download",
                             timeout=30)
            r.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    raise ValueError("bad path in bundle")
            zf.extractall(dest)
        runsh = os.path.join(dest, "run.sh")
        if os.path.exists(runsh):
            os.chmod(runsh, 0o755)
        meta = os.path.join(dest, "app.json")
        if not os.path.exists(meta):
            raise ValueError("bundle missing app.json")
        return True, "Installed!"
    except Exception as e:
        shutil.rmtree(dest, ignore_errors=True)
        return False, f"Install failed: {e}"


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

        hints = [("A", "Pick it up"), ("B", "Home")] if n else [("B", "Home")]
        self.draw_footer(hints)

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
                        if self.state == "DETAIL":
                            self.state = "SHELF"
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
            elif self.state == "INSTALLING":
                self.draw_installing(t)
            elif self.state == "DONE":
                self.draw_done(t)
            pygame.display.flip()
            self.clock.tick(30)


if __name__ == "__main__":
    Appmart().run()
