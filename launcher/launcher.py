#!/usr/bin/env python3
"""Muse Boy home screen - aurora theme.

Fullscreen app grid from /opt/gpi/apps/*/app.json.
D-pad navigates, A launches. Select (F13, global) kills the running app
and returns here from anywhere -- the launcher supervises every child
in its own process group so grandchildren (e.g. Chromium) die too.
The grid rescans on every return, so newly installed apps appear
without a restart.
"""
import hashlib
import json
import math
import os
import random
import signal
import subprocess
import sys
import threading
import time

import pygame

sys.path.insert(0, "/opt/gpi/common")
from gpi_ui import GlobalKeyWatcher, W, H, BLACK, WHITE, DIM, DARK
from evdev import ecodes

APPS_DIR = "/opt/gpi/apps"

AMBER = (255, 176, 64)
AMBER_SOFT = (255, 190, 90)
CYAN = (110, 200, 250)
INK = (10, 14, 24)

# tile geometry
COLS, ROWS = 3, 2
PER_PAGE = COLS * ROWS
TW, TH, GAP = 184, 150, 18
GX0 = (W - (COLS * TW + (COLS - 1) * GAP)) // 2
GY0 = 108


def load_font(size):
    for name in ("dejavusans", "freesans", "liberationsans"):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            pass
    return pygame.font.Font(None, size)


def vgrad(w, h, top, bottom, edge=None, radius=20):
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        f = y / max(1, h - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
        pygame.draw.line(surf, c, (0, y), (w, y))
    # round the corners by masking
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h),
                     border_radius=radius)
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    if edge:
        pygame.draw.rect(surf, edge, (0, 0, w, h), 2, border_radius=radius)
    return surf


def proc_icon(letter, size=76):
    """Procedural app icon: gradient tile + first letter."""
    hues = [(255, 122, 89), (110, 200, 250), (150, 130, 255),
            (90, 220, 130), (255, 190, 80), (255, 130, 180)]
    h = hues[int(hashlib.md5(letter.encode()).hexdigest(), 16) % len(hues)]
    dark = tuple(int(c * 0.45) for c in h)
    icon = vgrad(size, size, h, dark, radius=20)
    f = load_font(int(size * 0.52))
    t = f.render(letter.upper(), True, (255, 255, 255))
    # soft shadow behind letter
    sh = f.render(letter.upper(), True, (0, 0, 0, 90))
    icon.blit(sh, sh.get_rect(center=(size // 2 + 2, size // 2 + 3)))
    icon.blit(t, t.get_rect(center=(size // 2, size // 2)))
    return icon


class Launcher:
    def __init__(self):
        pygame.init()
        pygame.mouse.set_visible(False)
        self.screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
        self.window_id = pygame.display.get_wm_info().get("window")
        pygame.display.set_caption("Muse Boy")
        self.f_logo = load_font(34)
        self.f_clock = load_font(38)
        self.f_date = load_font(17)
        self.f_name = load_font(21)
        self.f_small = load_font(17)
        self.f_hint = load_font(16)
        self.child = None
        self.home_requested = threading.Event()
        self.icon_cache = {}
        self.proc_cache = {}
        self.name_cache = {}
        self.t0 = time.time()
        self.watcher = GlobalKeyWatcher(self._on_global_key)

        # background gradient
        self.bg = pygame.Surface((W, H))
        top, bot = (10, 14, 34), (26, 20, 58)
        for y in range(H):
            f = y / H
            c = tuple(int(top[i] + (bot[i] - top[i]) * f) for i in range(3))
            pygame.draw.line(self.bg, c, (0, y), (W, y))
        # stars
        rnd = random.Random(7)
        self.stars = [(rnd.randint(0, W), rnd.randint(0, H),
                       rnd.choice([1, 1, 2]), rnd.uniform(0, 6.28),
                       rnd.uniform(0.6, 1.8)) for _ in range(110)]
        # aurora blobs (x, y, rx, ry, color, phase, speed)
        self.blobs = [
            (150, 130, 220, 120, (40, 120, 160), 0.0, 0.10),
            (480, 200, 260, 140, (90, 60, 160), 2.1, 0.07),
            (320, 420, 300, 130, (160, 110, 40), 4.2, 0.09),
        ]
        self.aurora = pygame.Surface((W, H), pygame.SRCALPHA)

        # tile art (pre-rendered)
        self.tile_bg = vgrad(TW, TH, (36, 44, 70), (22, 28, 50),
                             edge=(70, 84, 120), radius=22)
        self.tile_sel = vgrad(TW, TH, (88, 66, 34), (48, 36, 22),
                              edge=AMBER, radius=22)
        # soft glow behind selected tile
        self.glow = pygame.Surface((TW + 36, TH + 36), pygame.SRCALPHA)
        for i, a in ((18, 26), (12, 40), (6, 60)):
            pygame.draw.rect(self.glow, (255, 176, 64, a),
                             (i, i, TW + 36 - 2 * i, TH + 36 - 2 * i),
                             border_radius=28)

    # ---- child process supervision ----
    def _on_global_key(self, code):
        # The input watcher is a worker thread: request Home here, then let the
        # launch loop stop children and restore focus on the UI thread.
        if code == ecodes.KEY_F13 and self.child is not None:
            self.home_requested.set()

    def restore_focus(self):
        pygame.event.clear()
        if self.window_id is None:
            return
        try:
            subprocess.run(["xdotool", "windowfocus", "--sync",
                            str(self.window_id)], check=True, timeout=2,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def cleanup_hn_browser(self, app):
        """Close HN's separate Chromium session when its app exits."""
        if app.get("id") != "hn":
            return
        pattern = (r"^/usr/lib/chromium/chromium .*"
                   r"--user-data-dir=/home/tendo/.config/gpi-chromium")
        try:
            subprocess.run(["pkill", "-TERM", "-f", "--", pattern],
                           timeout=2, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception:
            pass
        deadline = time.time() + 1.5
        while time.time() < deadline:
            try:
                found = subprocess.run(["pgrep", "-f", "--", pattern],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                if found.returncode != 0:
                    return
            except Exception:
                return
            time.sleep(0.1)
        try:
            subprocess.run(["pkill", "-KILL", "-f", "--", pattern],
                           timeout=2, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _kill_child(self):
        child, self.child = self.child, None
        if child is None:
            return
        try:
            pgid = os.getpgid(child.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + 1.5
        while time.time() < deadline and child.poll() is None:
            time.sleep(0.05)
        if child.poll() is None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def scan_apps(self):
        apps = []
        if os.path.isdir(APPS_DIR):
            for entry in sorted(os.listdir(APPS_DIR)):
                mf = os.path.join(APPS_DIR, entry, "app.json")
                if os.path.isfile(mf):
                    try:
                        with open(mf) as f:
                            info = json.load(f)
                        info["_dir"] = os.path.join(APPS_DIR, entry)
                        apps.append(info)
                    except Exception:
                        pass
        return apps

    # ---- icons ----
    def get_icon(self, app, size=72):
        key = (app.get("_dir", ""), size)
        if key in self.icon_cache:
            return self.icon_cache[key]
        icon = None
        for ext in ("png", "jpg", "webp"):
            path = os.path.join(app.get("_dir", ""), "icon." + ext)
            if os.path.isfile(path):
                try:
                    img = pygame.image.load(path).convert_alpha()
                    icon = pygame.transform.smoothscale(img, (size, size))
                    break
                except Exception:
                    pass
        if icon is None:
            ck = (app.get("name", "?")[:1], size)
            if ck not in self.proc_cache:
                self.proc_cache[ck] = proc_icon(ck[0], size)
            icon = self.proc_cache[ck]
        self.icon_cache[key] = icon
        return icon

    def name_surf(self, text, selected):
        key = (text, selected)
        if key not in self.name_cache:
            self.name_cache[key] = self.f_name.render(
                text, True, WHITE if selected else (205, 210, 225))
        return self.name_cache[key]

    # ---- drawing ----
    def draw_bg(self):
        s = self.screen
        s.blit(self.bg, (0, 0))
        t = time.time() - self.t0
        # aurora blobs
        self.aurora.fill((0, 0, 0, 0))
        for x, y, rx, ry, col, ph, sp in self.blobs:
            dx = math.sin(t * sp + ph) * 46
            pygame.draw.ellipse(self.aurora, col + (16,),
                                (x - rx + dx, y - ry, rx * 2, ry * 2))
        s.blit(self.aurora, (0, 0))
        # twinkling stars
        for x, y, r, ph, sp in self.stars:
            a = 90 + int(80 * math.sin(t * sp + ph))
            if a > 40:
                pygame.draw.circle(s, (200, 215, 255), (x, y), r)

    def bolt_head(self, x, y, r=20):
        """Tiny Bolt head logo."""
        s = self.screen
        pygame.draw.circle(s, (168, 212, 245), (x, y), r)
        pygame.draw.arc(s, AMBER, (x - r, y - r - 7, r * 2, r * 2),
                        math.pi * 0.08, math.pi * 0.92, 9)
        pygame.draw.rect(s, AMBER, (x - r - 4, y - 8, r * 2 + 8, 5),
                         border_radius=2)
        er, eo = 8, 8
        for sgn in (-1, 1):
            pygame.draw.circle(s, WHITE, (x + sgn * eo, y - 2), er)
            pygame.draw.circle(s, INK, (x + sgn * eo, y - 2), 4)
        pygame.draw.arc(s, INK, (x - 8, y + 1, 16, 11),
                        math.pi * 0.15, math.pi * 0.85, 2)

    def header(self):
        s = self.screen
        # logo: bolt head + Muse Boy wordmark
        self.bolt_head(40, 34, 19)
        t = self.f_logo.render("Muse Boy", True, WHITE)
        s.blit(t, (68, 14))
        t = self.f_date.render("home console", True, DIM)
        s.blit(t, (70, 50))
        # clock + date, right aligned
        now = time.localtime()
        clock = time.strftime("%-I:%M", now) + time.strftime("%p", now).lower()
        t = self.f_clock.render(clock, True, WHITE)
        s.blit(t, t.get_rect(topright=(W - 24, 10)))
        date = time.strftime("%a %b %-d", now)
        t = self.f_date.render(date, True, AMBER_SOFT)
        s.blit(t, t.get_rect(topright=(W - 24, 52)))
        pygame.draw.line(s, (60, 72, 110), (24, 78), (W - 24, 78), 1)

    def footer(self):
        y = H - 46
        pygame.draw.line(self.screen, (60, 72, 110), (24, y), (W - 24, y), 1)
        hints = [("D-pad", "move"), ("A", "open"), ("Select", "home")]
        x = 28
        for key, label in hints:
            kw = self.f_hint.size(key)[0] + 20
            pygame.draw.rect(self.screen, (50, 62, 96),
                             (x, y + 10, kw, 28), border_radius=8)
            t = self.f_hint.render(key, True, AMBER)
            self.screen.blit(t, (x + 10, y + 13))
            t = self.f_hint.render(label, True, DIM)
            self.screen.blit(t, (x + kw + 8, y + 13))
            x += kw + self.f_hint.size(label)[0] + 34

    def draw_grid(self, apps, sel):
        s = self.screen
        self.draw_bg()
        self.header()
        t = time.time() - self.t0

        page = sel // PER_PAGE if apps else 0
        pages = max(1, (len(apps) + PER_PAGE - 1) // PER_PAGE)
        visible = apps[page * PER_PAGE:(page + 1) * PER_PAGE]

        for i, app in enumerate(visible):
            gi = page * PER_PAGE + i
            cx, cy = i % COLS, i // COLS
            x = GX0 + cx * (TW + GAP)
            y = GY0 + cy * (TH + GAP + 8)
            active = gi == sel
            lift = -6 if active else 0
            if active:
                pulse = 150 + int(60 * math.sin(t * 3))
                glow = self.glow.copy()
                glow.set_alpha(pulse)
                s.blit(glow, (x - 18, y - 18 + lift))
                s.blit(self.tile_sel, (x, y + lift))
            else:
                s.blit(self.tile_bg, (x, y))
            # icon with shadow
            icon = self.get_icon(app)
            ix = x + (TW - icon.get_width()) // 2
            iy = y + 18 + lift
            sh = pygame.Surface(icon.get_size(), pygame.SRCALPHA)
            pygame.draw.rect(sh, (0, 0, 0, 70), (3, 4) + icon.get_size(),
                             border_radius=18)
            s.blit(sh, (ix, iy))
            s.blit(icon, (ix, iy))
            # name
            name = app.get("name", "?")
            if len(name) > 16:
                name = name[:15] + "…"
            nt = self.name_surf(name, active)
            s.blit(nt, nt.get_rect(center=(x + TW // 2, y + TH - 26 + lift)))

        if not apps:
            t = self.f_name.render("No apps yet — ask Bolt to build one!",
                                   True, DIM)
            s.blit(t, t.get_rect(center=(W // 2, 250)))

        # selected app description strip
        if apps:
            desc = apps[sel].get("description", "")[:64]
            if desc:
                t = self.f_small.render(desc, True, DIM)
                s.blit(t, t.get_rect(center=(W // 2, GY0 + 2 * (TH + GAP + 8) - 6)))

        # pagination dots
        if pages > 1:
            dx = W // 2 - (pages - 1) * 12
            for p in range(pages):
                c = AMBER if p == page else (70, 80, 110)
                pygame.draw.circle(s, c, (dx + p * 24, H - 62), 5)

        # mini bolt wave in the corner when space allows
        self.footer()
        pygame.display.flip()

    def draw_splash(self, app, text):
        s = self.screen
        self.draw_bg()
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((5, 8, 18, 160))
        s.blit(dim, (0, 0))
        r = pygame.Rect(W // 2 - 170, H // 2 - 110, 340, 220)
        pygame.draw.rect(s, (30, 38, 62), r, border_radius=24)
        pygame.draw.rect(s, AMBER, r, 2, border_radius=24)
        icon = self.get_icon(app, 88)
        s.blit(icon, icon.get_rect(center=(W // 2, H // 2 - 48)))
        t = self.f_name.render(app.get("name", "?")[:24], True, WHITE)
        s.blit(t, t.get_rect(center=(W // 2, H // 2 + 22)))
        dots = "." * (1 + int((time.time() * 2) % 3))
        t = self.f_small.render(text + dots, True, DIM)
        s.blit(t, t.get_rect(center=(W // 2, H // 2 + 58)))
        pygame.display.flip()

    def launch(self, app):
        exe = app.get("exec")
        if not exe or not os.path.isfile(exe):
            self.draw_splash(app, "Missing executable")
            time.sleep(1.2)
            return
        self.draw_splash(app, "Opening")
        try:
            self.home_requested.clear()
            child = subprocess.Popen(
                [exe], cwd=app["_dir"], start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.child = child
        except Exception:
            self.draw_splash(app, "Launch failed")
            time.sleep(1.5)
            self.child = None
            return
        # Keep the splash up briefly, then supervise until exit or Home.
        time.sleep(0.4)
        while child.poll() is None and not self.home_requested.is_set():
            time.sleep(0.1)
        if self.home_requested.is_set():
            self._kill_child()
        elif self.child is child:
            self.child = None
        self.cleanup_hn_browser(app)
        self.restore_focus()
        self.home_requested.clear()

    def run(self):
        sel = 0
        clock = pygame.time.Clock()
        last_n = -1
        self.restore_focus()
        while True:
            apps = self.scan_apps()
            if len(apps) != last_n:
                self.icon_cache.clear()
                self.name_cache.clear()
                last_n = len(apps)
            if sel >= len(apps):
                sel = max(0, len(apps) - 1)
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    return
                if ev.type == pygame.KEYDOWN:
                    n = len(apps)
                    if n == 0:
                        continue
                    if ev.key == pygame.K_UP:
                        sel = (sel - COLS) % n
                    elif ev.key == pygame.K_DOWN:
                        sel = (sel + COLS) % n
                    elif ev.key == pygame.K_LEFT:
                        sel = (sel - 1) % n
                    elif ev.key == pygame.K_RIGHT:
                        sel = (sel + 1) % n
                    elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self.launch(apps[sel])
            self.draw_grid(apps, sel)
            clock.tick(20)  # keep passive-cooled handheld thermals in check


if __name__ == "__main__":
    Launcher().run()
