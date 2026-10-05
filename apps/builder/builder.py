#!/usr/bin/env python3
"""App Builder starring Bolt, your builder bot.

Bolt listens to your idea, shows you his build plan, and gets to work.
A: record a new app idea (up to 20s, B stops early)
X: pick an installed app, then record the change you want
Select is global home (launcher).
"""
import array
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

import pygame

sys.path.insert(0, "/opt/gpi/common")
sys.path.insert(0, os.path.dirname(__file__))
from gpi_ui import W, H, BLACK, WHITE, DIM, DARK
from bolt import Bolt
from local_plan import usable_transcript

# ---- palette ----
BG_TOP = (8, 12, 24)
BG_BOT = (18, 26, 46)
GRID = (26, 36, 60)
PANEL = (22, 30, 52)
PANEL_EDGE = (58, 76, 116)
AMBER = (255, 176, 32)
AMBER_DIM = (150, 105, 25)
CYAN = (110, 200, 250)
GREEN = (90, 220, 130)
RED = (255, 100, 100)
INK = (10, 14, 24)
BUBBLE = (240, 244, 252)

REQ_DIR = "/var/lib/gpi-builder/requests"
BUILD_DIR = "/var/lib/gpi-builder/builds"
TMP_DIR = "/var/lib/gpi-builder/.tmp"
APPS_DIR = "/opt/gpi/apps"
APP_MART_RUNNER = "/opt/gpi/apps/appmart/run.sh"
MAX_REC = 60
LOCAL_PLAN = "/opt/gpi/apps/builder/local_plan.py"
MUSEGADGET_BIN = "/usr/local/bin/musegadget"
NOTIFY_DIR = "/var/lib/gpi-builder/.tmp/notify-pending"


def pcm_to_wav(raw_path, wav_path):
    """Wrap the Builder's 16 kHz mono PulseAudio capture in a WAV container."""
    with open(raw_path, "rb") as source, wave.open(wav_path, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        while True:
            block = source.read(65536)
            if not block:
                break
            target.writeframesraw(block[:len(block) - (len(block) % 2)])
        target.writeframes(b"")


def pcm_peak_level(data):
    """Convert a little-endian signed 16-bit PCM block to a 0–100 meter."""
    samples = array.array("h")
    samples.frombytes(data[:len(data) - (len(data) % 2)])
    peak = max((abs(sample) for sample in samples), default=0)
    if peak < 1:
        return 0
    dbfs = 20 * math.log10(min(32768, peak) / 32768.0)
    return max(0, min(100, int((dbfs + 55) * 100 / 52)))


def load_font(size):
    for name in ("dejavusans", "freesans", "liberationsans"):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            pass
    return pygame.font.Font(None, size)


def rounded(surf, rect, color, radius=14, width=0, edge=None):
    pygame.draw.rect(surf, color, rect, width, border_radius=radius)
    if edge:
        pygame.draw.rect(surf, edge, rect, 2, border_radius=radius)


def speech_bubble(surf, font, text, rect, tail_from, max_w=20):
    """White bubble with wrapped dark text and a tail triangle."""
    rounded(surf, rect, BUBBLE, radius=16)
    # tail
    cx, cy = rect.centerx, rect.bottom
    pygame.draw.polygon(surf, BUBBLE,
                        [(cx - 14, rect.bottom - 2), (cx + 14, rect.bottom - 2),
                         tail_from])
    words, lines, line = text.split(), [], ""
    for w_ in words:
        t = (line + " " + w_).strip()
        if font.size(t)[0] > rect.w - 28 and line:
            lines.append(line)
            line = w_
        else:
            line = t
    if line:
        lines.append(line)
    y = rect.y + 14
    for ln in lines[:5]:
        t = font.render(ln, True, INK)
        surf.blit(t, (rect.x + 14, y))
        y += font.get_linesize()


def keycap(surf, font, key, label, x, y):
    kw = font.size(key)[0] + 22
    rounded(surf, (x, y, kw, 34), PANEL_EDGE, radius=8)
    rounded(surf, (x + 2, y + 2, kw - 4, 30), PANEL, radius=6)
    t = font.render(key, True, AMBER)
    surf.blit(t, (x + 11, y + 5))
    t2 = font.render(label, True, DIM)
    surf.blit(t2, (x + kw + 10, y + 5))
    return kw + font.size(label)[0] + 32


class BuilderApp:
    def __init__(self):
        pygame.init()
        pygame.mouse.set_visible(False)
        self.screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
        pygame.display.set_caption("App Builder")
        self.f_title = load_font(30)
        self.f_item = load_font(23)
        self.f_small = load_font(18)
        self.f_bubble = load_font(20)
        self.state = "IDLE"
        self.reqid = None
        self.edit_target = None
        self.addendum_to = None
        self.rec_proc = None
        self.rec_file = None
        self.rec_reader = None
        self.rec_level_lock = threading.Lock()
        self.rec_level = 0
        self.rec_levels = [0] * 28
        self.rec_raw_path = None
        self.rec_start = 0
        self.status = {}
        self.queued = False
        self.local_failure = False
        self.current_plan = None
        self.context_spec = None
        self.plan_scroll = 0
        self.plan_pool = ThreadPoolExecutor(max_workers=1)
        self.plan_future = None
        self.plan_progress_file = None
        self.plan_processes = {}
        self.plan_cancel_events = {}
        self.plan_request_id = None
        self.poll_at = 0
        self.wait_start = 0
        self.auto_approved_request = None
        self.auto_approval_error = ""
        self.notify_retry_at = 0
        self.notify_inflight = False
        self.muse_notify_error = ""
        self.apps = []
        self.pick_sel = 0
        self.jobs = []
        self.job_sel = 0
        self.recovered_local_job = False
        self.bolt = Bolt()
        # pre-rendered background
        self.bg = pygame.Surface((W, H))
        for y in range(H):
            f = y / H
            c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * f)
                      for i in range(3))
            pygame.draw.line(self.bg, c, (0, y), (W, y))
        for gx in range(0, W, 44):
            pygame.draw.line(self.bg, GRID, (gx, 0), (gx, H))
        for gy in range(0, H, 44):
            pygame.draw.line(self.bg, GRID, (0, gy), (W, gy))
        for d in (REQ_DIR, TMP_DIR):
            os.makedirs(d, exist_ok=True)

    def header(self):
        # top bar
        pygame.draw.rect(self.screen, (12, 18, 34), (0, 0, W, 54))
        pygame.draw.line(self.screen, PANEL_EDGE, (0, 54), (W, 54), 2)
        # little wrench-ish badge
        pygame.draw.circle(self.screen, AMBER, (36, 27), 15)
        t = self.f_small.render("B", True, INK)
        self.screen.blit(t, t.get_rect(center=(36, 28)))
        t = self.f_title.render("App Builder", True, WHITE)
        self.screen.blit(t, (62, 10))
        t = self.f_small.render("starring Bolt", True, DIM)
        self.screen.blit(t, (62 + self.f_title.size("App Builder")[0] + 12, 20))

    def footer_hints(self, hints):
        # hints: list of (key, label)
        y = H - 52
        pygame.draw.rect(self.screen, (12, 18, 34), (0, y, W, 52))
        pygame.draw.line(self.screen, PANEL_EDGE, (0, y), (W, y), 2)
        x = 24
        for key, label in hints:
            x += keycap(self.screen, self.f_small, key, label, x, y + 9) + 18

    def plan_lines(self):
        spec = self.current_plan
        rows = []
        if not spec:
            text = self.status.get("plan", "")
            return text.splitlines() or [text]
        if isinstance(spec, dict) and set(spec) == {"transcript"}:
            rows.extend(("I HEARD:", "") + tuple(str(spec["transcript"]).splitlines()))
        elif self.edit_target:
            target = next((app.get("name", self.edit_target) for app in self.apps
                           if app.get("id") == self.edit_target), self.edit_target)
            rows.append(f"CHANGE INSTALLED APP: {target}")
        elif "app_name" in spec:
            rows.extend((f"YOU SAID: {spec.get('transcript', '')}",
                         f"APP: {spec.get('app_name', '?')} ({spec.get('app_id', '?')})",
                         f"ABOUT: {spec.get('description', '?')}"))
            rows.extend(f"FEATURE: {item}" for item in spec.get("features", []))
            rows.extend(f"DATA: {item}" for item in spec.get("data_sources", []))
        elif "title" in spec:
            rows.extend((f"APP: {spec.get('title', '?')}",
                         f"GOAL: {spec.get('goal', '?')}"))
            for index, item in enumerate(spec.get("user_flow", []), 1):
                rows.append(f"FLOW {index}: {item}")
            arrows = (("up", "UP ↑"), ("down", "DOWN ↓"),
                      ("left", "LEFT ←"), ("right", "RIGHT →"),
                      ("A", "A"), ("B", "B"), ("X", "X"),
                      ("Y", "Y"), ("Start", "START"))
            for screen in spec.get("screens", []):
                rows.extend((f"SCREEN: {screen.get('name', '?')}",
                             f"Purpose: {screen.get('purpose', '?')}"))
                for key in ("normal", "empty", "loading", "error"):
                    rows.append(f"{key.upper()}: {screen.get('states', {}).get(key, '?')}")
                for key, label in arrows:
                    rows.append(f"{label}: {screen.get('controls', {}).get(key, '?')}")
            for source in spec.get("data_sources", []):
                if isinstance(source, dict):
                    rows.extend((f"DATA: {source.get('name', '?')}",
                                 f"URL: {source.get('endpoint', '?')}",
                                 f"PARAMS: {source.get('params', '?')}",
                                 f"TIMEOUT: {source.get('timeout_seconds', '?')}s",
                                 f"OFFLINE: {source.get('network_failure_behavior', '?')}"))
                else:
                    rows.append(f"DATA: {source}")
            rows.append(f"INPUT: {spec.get('input_constraints', '?')}")
            rows.append(f"ICON: {spec.get('icon_art_direction', '?')}")
            rows.extend(f"OUT OF SCOPE: {item}" for item in spec.get("out_of_scope", []))
            rows.extend(f"ACCEPT: {item}" for item in spec.get("acceptance_checks", []))
            rows.extend(f"OPEN QUESTION: {item}" for item in spec.get("open_questions", []))
        result = []
        for row in rows:
            words, line = str(row).split(), ""
            for word in words:
                candidate = (line + " " + word).strip()
                if self.f_small.size(candidate)[0] > 338 and line:
                    result.append(line)
                    line = word
                else:
                    line = candidate
            if line:
                result.append(line)
        return result or ["No plan details were returned."]

    def draw(self):
        s = self.screen
        s.blit(self.bg, (0, 0))
        self.header()
        st = self.state

        if st == "IDLE":
            self.bolt.draw(s, 150, 250, "idle", 1.0)
            speech_bubble(s, self.f_bubble,
                          "Hey! I'm Bolt. Voice stays local; only the plan goes to Muse.",
                          pygame.Rect(250, 120, 350, 110), (170, 200))
            # two big option cards
            for i, (key, title, sub) in enumerate(
                    (("A", "Speak an idea",
                      "Describe the app you want"),
                     ("X", "Change an app",
                      "Pick one and tell me what's new"))):
                r = pygame.Rect(250, 250 + i * 92, 350, 80)
                rounded(s, r, PANEL, edge=PANEL_EDGE)
                t = self.f_item.render(title, True, WHITE)
                s.blit(t, (r.x + 70, r.y + 12))
                t = self.f_small.render(sub, True, DIM)
                s.blit(t, (r.x + 70, r.y + 42))
                kw = self.f_item.size(key)[0] + 24
                rounded(s, (r.x + 16, r.y + 20, kw, 40), AMBER, radius=10)
                t = self.f_item.render(key, True, INK)
                s.blit(t, t.get_rect(center=(r.x + 16 + kw // 2, r.y + 40)))
            self.footer_hints([("Y", "jobs"), ("Select", "home")])

        elif st == "JOBS":
            self.bolt.draw(s, 150, 250, "think", 0.9)
            speech_bubble(s, self.f_bubble,
                          "Your recent local plans and Muse builds stay here.",
                          pygame.Rect(250, 92, 350, 76), (170, 190))
            if not self.jobs:
                rounded(s, pygame.Rect(250, 200, 350, 100), PANEL, edge=PANEL_EDGE)
                t = self.f_item.render("No saved jobs yet", True, WHITE)
                s.blit(t, t.get_rect(center=(425, 245)))
            else:
                for i, job in enumerate(self.jobs[:5]):
                    rect = pygame.Rect(250, 180 + i * 48, 350, 42)
                    selected = i == self.job_sel
                    rounded(s, rect, PANEL_EDGE if selected else PANEL,
                            edge=AMBER if selected else None)
                    title = job.get("title") or job.get("id", "Job")
                    stage = job.get("stage", "saved")
                    label = (str(title)[:21] + " · " + str(stage)[:14])
                    t = self.f_small.render(label, True, WHITE if selected else DIM)
                    s.blit(t, (rect.x + 12, rect.y + 11))
            self.footer_hints([("A", "open"), ("B", "back")])

        elif st == "PICK":
            self.bolt.draw(s, 150, 250, "idle", 0.9)
            speech_bubble(s, self.f_bubble, "Which app gets an upgrade?",
                          pygame.Rect(250, 100, 350, 76), (170, 190))
            y = 196
            for i, a in enumerate(self.apps[:6]):
                sel = i == self.pick_sel
                r = pygame.Rect(250, y, 350, 40)
                rounded(s, r, PANEL_EDGE if sel else PANEL,
                        edge=AMBER if sel else None)
                t = self.f_small.render(a.get("name", "?")[:28], True,
                                        WHITE if sel else DIM)
                s.blit(t, (r.x + 16, r.y + 9))
                y += 48
            self.footer_hints([("A", "pick"), ("B", "back")])

        elif st == "REC":
            secs = int(time.time() - self.rec_start)
            self.bolt.draw(s, 150, 250, "listen", 1.05)
            speech_bubble(s, self.f_bubble,
                          "Listening... %ds / %ds" % (secs, MAX_REC),
                          pygame.Rect(250, 120, 350, 76), (170, 200))
            # Bars come from the same PCM stream written to the local recording.
            bx, bw = 250, 350
            pygame.draw.rect(s, PANEL, (bx, 230, bw, 120), border_radius=14)
            with self.rec_level_lock:
                levels = list(self.rec_levels)
                level = self.rec_level
            bars = len(levels)
            for i, value in enumerate(levels):
                hgt = 5 + value * 0.62
                col = (CYAN if self.addendum_to else
                       GREEN if value >= 9 else AMBER_DIM)
                x = bx + 14 + i * ((bw - 28) / bars)
                pygame.draw.rect(s, col,
                                 (x, 290 - hgt / 2, (bw - 28) / bars - 4,
                                  max(4, hgt)),
                                 border_radius=3)
            activity = ("MIC ACTIVE  %d%%" % level if level >= 9
                        else "SPEAK TO TEST MIC")
            t = self.f_small.render(activity + ("  ·  adding detail"
                                                if self.addendum_to else
                                                "  ·  listening"),
                                    True, DIM)
            s.blit(t, t.get_rect(center=(bx + bw // 2, 380)))
            self.footer_hints([("B", "stop")])

        elif st == "PREVIEW":
            self.bolt.draw(s, 130, 240, "think", 0.95)
            transcript_only = (isinstance(self.current_plan, dict) and
                               set(self.current_plan) == {"transcript"})
            prompt = ("Check Whisper's exact words. D-pad scrolls; X records again."
                      if transcript_only else "Review the full plan. D-pad scrolls; B always goes back.")
            speech_bubble(s, self.f_bubble, prompt,
                          pygame.Rect(230, 76, 380, 70), (150, 175))
            # blueprint card with the plan
            r = pygame.Rect(230, 160, 380, 230)
            rounded(s, r, PANEL, edge=AMBER)
            t = self.f_small.render("VOICE TRANSCRIPT" if transcript_only else "BUILD PLAN", True, AMBER)
            s.blit(t, (r.x + 18, r.y + 12))
            pygame.draw.line(s, PANEL_EDGE, (r.x + 18, r.y + 38),
                             (r.x + r.w - 18, r.y + 38), 2)
            lines = self.plan_lines()
            y = r.y + 52
            visible = 7
            self.plan_scroll = max(0, min(self.plan_scroll,
                                          max(0, len(lines) - visible)))
            for ln in lines[self.plan_scroll:self.plan_scroll + visible]:
                t = self.f_small.render(ln, True, WHITE)
                s.blit(t, (r.x + 18, y))
                y += 26
            page = f"{self.plan_scroll + 1}-{min(len(lines), self.plan_scroll + visible)}/{len(lines)}"
            t = self.f_small.render(page, True, DIM)
            s.blit(t, t.get_rect(topright=(r.right - 12, r.y + 12)))
            self.footer_hints([("A", "send to Muse"), ("X", "record again"),
                               ("B", "cancel")])

        elif st == "WAIT":
            stage = self.status.get("stage", "queued")
            msg = self.status.get("message", "Waiting for Muse to report its next step.")
            mood = ("listen" if stage in ("transcribing", "local-transcription") else
                    "think" if stage in ("local-plan", "planning", "testing", "validating") else
                    "build" if stage in ("building", "coding", "installing", "deploying") else
                    "idle")
            self.bolt.draw(s, 150, 250, mood, 1.0)
            bubble = {
                "local-transcription": "Transcribing on the GPi. Your audio stays here.",
                "transcribing": "Transcribing on the GPi. Your audio stays here.",
                "local-plan": "Gemma is drafting the app contract locally.",
                "planning": "Gemma is drafting the app plan locally.",
                "sending": "Sending only the local transcript to Muse.",
                "waiting": "Transcript sent. Waiting for Muse's first status report.",
                "preview": "Muse received it and is reviewing the request.",
                "coding": "Muse reports it is writing the app.",
                "building": "Muse reports it is building the app.",
                "testing": "Muse reports it is testing the app.",
                "validating": "Muse reports it is checking the app.",
                "installing": "Muse reports it is installing the app.",
                "deploying": "Muse reports it is installing the app.",
            }.get(stage, "Waiting for Muse's next status report.")
            if self.auto_approved_request == self.reqid and stage == "preview":
                bubble = "Your transcript approved this plan. Waiting for Muse to start building."
            speech_bubble(s, self.f_bubble, bubble,
                          pygame.Rect(250, 120, 350, 76), (170, 200))
            r = pygame.Rect(250, 230, 350, 130)
            rounded(s, r, PANEL, edge=PANEL_EDGE)
            t = self.f_small.render("stage: " + stage, True, AMBER)
            s.blit(t, (r.x + 18, r.y + 14))
            # Keep the clock anchored to the request, and show the age of Muse's
            # actual report separately. Never turn elapsed time into fake progress.
            submitted = self.status.get("submitted_at") or self.wait_start
            try:
                elapsed = max(0, int(time.time() - float(submitted)))
            except (TypeError, ValueError, OverflowError):
                elapsed = 0
            elapsed_text = self.f_small.render("waiting %d:%02d" %
                                               (elapsed // 60, elapsed % 60), True, DIM)
            s.blit(elapsed_text, elapsed_text.get_rect(topright=(r.right - 16, r.y + 14)))
            current = self.status.get("current_step", "")
            if self.auto_approved_request == self.reqid and stage == "preview":
                current = "Transcript approved the plan automatically"
                msg = "Waiting for Muse to start building."
            elif self.auto_approval_error and stage == "preview":
                msg = "Could not send approval yet: " + self.auto_approval_error
            if current:
                msg = current + " — " + msg
            try:
                updated = float(self.status.get("updated", 0))
            except (TypeError, ValueError):
                updated = 0
            report_age = max(0, int(time.time() - updated)) if updated else None
            # Allow the worker's roughly two-minute milestone window plus grace.
            stale = bool(self.queued and report_age is not None and report_age > 180)
            if stale:
                msg = "Muse last reported %s ago. %s" % (
                    self.format_age(report_age), msg)
            msg = msg[:140]
            words, line, y = msg.split(), "", r.y + 48
            for w_ in words:
                t_ = (line + " " + w_).strip()
                if self.f_small.size(t_)[0] > r.w - 40 and line:
                    t = self.f_small.render(line, True, DIM)
                    s.blit(t, (r.x + 18, y))
                    y += 26
                    if y > r.bottom - 30:
                        line = ""
                        break
                    line = w_
                else:
                    line = t_
            if line:
                t = self.f_small.render(line, True, DIM)
                s.blit(t, (r.x + 18, y))
            progress = self.status.get("progress")
            has_progress = (isinstance(progress, (int, float)) and
                            not isinstance(progress, bool) and 0 <= progress <= 100)
            if has_progress:
                bar = pygame.Rect(270, 377, 300, 12)
                pygame.draw.rect(s, DARK, bar, border_radius=6)
                if progress:
                    pygame.draw.rect(s, AMBER,
                                     (bar.x, bar.y, int(bar.w * progress / 100), bar.h),
                                     border_radius=6)
                pct = self.f_small.render(f"{int(progress)}% reported", True, DIM)
                s.blit(pct, pct.get_rect(center=(bar.centerx, 402)))
            else:
                # Indeterminate activity: the builder has not reported a percentage.
                ph = int(time.time() * 3) % 4
                for i in range(4):
                    c = AMBER if i == ph else DARK
                    pygame.draw.circle(s, c, (350 + i * 26, 389), 6)
                t = self.f_small.render("Muse has not reported a percentage", True, DIM)
                s.blit(t, t.get_rect(center=(W // 2, 406)))
            if report_age is not None and not stale:
                freshness = ("Muse update just now" if report_age < 5 else
                             "Muse updated %s ago" % self.format_age(report_age))
                t = self.f_small.render(freshness, True, DIM)
                s.blit(t, t.get_rect(center=(W // 2, 425)))
            self.footer_hints([("B", "hide job"), ("Select", "home")])

        elif st == "DONE":
            name = self.status.get("app_name", "your app")
            self.bolt.draw(s, 150, 250, "happy", 1.05)
            speech_bubble(s, self.f_bubble, "Done! Check your home screen!",
                          pygame.Rect(250, 120, 350, 76), (170, 200))
            r = pygame.Rect(250, 230, 350, 110)
            rounded(s, r, PANEL, edge=GREEN)
            t = self.f_item.render("'" + name[:26] + "'", True, GREEN)
            s.blit(t, t.get_rect(center=(r.centerx, r.y + 36)))
            t = self.f_small.render("is ready on your home screen", True, DIM)
            s.blit(t, t.get_rect(center=(r.centerx, r.y + 72)))
            hints = [("Select", "home"), ("A", "build another")]
            if self.app_ready_to_share():
                hints.append(("X", "share to App Mart"))
            self.footer_hints(hints)

        elif st == "ERROR":
            self.bolt.draw(s, 150, 250, "sad", 0.95)
            speech_bubble(s, self.f_bubble, "Hmm, that didn't work...",
                          pygame.Rect(250, 120, 350, 76), (170, 200))
            r = pygame.Rect(250, 230, 350, 110)
            rounded(s, r, PANEL, edge=RED)
            msg = self.status.get("message", "?")
            words, line, y = msg.split(), "", r.y + 20
            for w_ in words:
                t_ = (line + " " + w_).strip()
                if self.f_small.size(t_)[0] > r.w - 40 and line:
                    t = self.f_small.render(line, True, DIM)
                    s.blit(t, (r.x + 18, y))
                    y += 26
                    line = w_
                else:
                    line = t_
            if line:
                t = self.f_small.render(line, True, DIM)
                s.blit(t, (r.x + 18, y))
            hints = ([ ("A", "retry"), ("X", "record again"), ("B", "cancel") ]
                     if self.local_failure else [("A", "try again"), ("Select", "home")])
            self.footer_hints(hints)

        pygame.display.flip()

    # ---- local transcription, contract drafting, and Muse handoff ----
    def create_local_plan(self, audio_path, context, progress_path,
                          request_id, cancel_event):
        command = [sys.executable, LOCAL_PLAN, str(audio_path),
                   "--progress-file", str(progress_path), "--transcript-only"]
        if context:
            command.extend(("--context", json.dumps(context, separators=(",", ":"))))
        if cancel_event.is_set():
            raise RuntimeError("Local planning was cancelled")
        proc = subprocess.Popen(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        self.plan_processes[request_id] = proc
        if cancel_event.is_set():
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            stdout, stderr = proc.communicate(timeout=330)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.communicate(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                proc.kill()
                proc.communicate()
            raise RuntimeError("Local planning timed out; try a shorter idea")
        finally:
            if self.plan_processes.get(request_id) is proc:
                self.plan_processes.pop(request_id, None)
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            detail = (stderr or stdout or "No response from local planner")[-220:]
            raise RuntimeError(detail) from exc
        if proc.returncode or payload.get("error"):
            raise RuntimeError(payload.get("error", "Local planning failed"))
        return payload

    def start_local_plan(self):
        if self.plan_future and not self.plan_future.done():
            self.local_failure = False
            shutil.rmtree(os.path.join(TMP_DIR, self.reqid or ""), ignore_errors=True)
            self.state = "ERROR"
            self.status = {"message": "Finishing the cancelled local task. Press A to try again in a moment."}
            return
        stage = os.path.join(TMP_DIR, self.reqid)
        wav = os.path.join(stage, "audio.wav")
        self.plan_progress_file = os.path.join(stage, "progress.json")
        self.plan_request_id = self.reqid
        cancel_event = threading.Event()
        self.plan_cancel_events[self.reqid] = cancel_event
        self.plan_future = self.plan_pool.submit(
            self.create_local_plan, wav, self.context_spec,
            self.plan_progress_file, self.reqid, cancel_event)
        self.state = "WAIT"
        self.queued = False
        self.wait_start = time.time()
        self.status = {"stage": "transcribing",
                       "message": "Audio is processed on this GPi only.",
                       "updated": time.time()}
        self.write_local_job(stage, "transcribing", self.status["message"])

    def update_local_plan(self):
        if self.plan_future and self.plan_future.done():
            future, self.plan_future = self.plan_future, None
            finished_request_id = self.plan_request_id
            if finished_request_id != self.reqid:
                try:
                    future.result()
                except Exception:
                    pass
                self.plan_request_id = None
                self.plan_cancel_events.pop(finished_request_id, None)
                return
            stage = os.path.join(TMP_DIR, self.reqid)
            try:
                result = future.result()
                plan = result["plan"]
                transcript_path = os.path.join(stage, "transcript.txt")
                if isinstance(plan, dict) and set(plan) == {"transcript"}:
                    with open(transcript_path, "w", encoding="utf-8") as f:
                        f.write(plan["transcript"])
                meta = {"created": time.time(), "audio_sent": False,
                        "planner": result.get("planner"),
                        "planning_seconds": result.get("planning_seconds"),
                        "transcription_only": result.get("transcription_only", False),
                        "transcript_file": "transcript.txt",
                        "progress_protocol": result.get("progress_protocol")}
                with open(os.path.join(stage, "meta.json"), "w") as f:
                    json.dump(meta, f)
                # The recording is only for local transcription. Never put it in
                # the request folder Muse can read.
                try:
                    os.unlink(os.path.join(stage, "audio.wav"))
                except FileNotFoundError:
                    pass
                self.current_plan = plan
                self.plan_scroll = 0
                self.local_failure = False
                self.addendum_to = None
                self.status = {
                    "stage": "preview",
                    "message": "Review the exact words Whisper heard before sending to Muse.",
                    "updated": time.time(),
                }
                self.write_local_job(stage, "preview", self.status["message"])
                self.state = "PREVIEW"
            except Exception as exc:
                self.local_failure = True
                self.state = "ERROR"
                self.status = {"message": ("Local plan failed: " + str(exc)[:145] +
                                           ". Audio stayed on this GPi; nothing was sent.")}
                self.write_local_job(stage, "error", self.status["message"])
            self.plan_cancel_events.pop(finished_request_id, None)
        elif self.plan_future and self.plan_progress_file:
            try:
                with open(self.plan_progress_file) as f:
                    progress = json.load(f)
                self.status.update(progress)
                if progress.get("step"):
                    self.status["stage"] = progress["step"]
                self.status["updated"] = time.time()
            except (OSError, ValueError):
                pass

    def start_recording(self, edit_target, context=None):
        self.edit_target = edit_target
        if context is not None:
            self.context_spec = dict(context)
        elif edit_target:
            self.context_spec = None
        else:
            self.context_spec = None
        if edit_target:
            target = next(({"id": app.get("id"), "name": app.get("name")}
                           for app in self.apps if app.get("id") == edit_target),
                          {"id": edit_target, "name": edit_target})
            if self.context_spec is None:
                self.context_spec = {}
            self.context_spec["target_app"] = target
        self.current_plan = None
        self.plan_scroll = 0
        self.local_failure = False
        self.queued = False
        self.reqid = "req-%d-%s" % (int(time.time()), uuid.uuid4().hex[:6])
        stage = os.path.join(TMP_DIR, self.reqid)
        os.makedirs(stage, exist_ok=True)
        self.write_local_job(stage, "recording", "Recording a new app idea on the GPi.")
        self.rec_raw_path = os.path.join(stage, "audio.pcm")
        self.rec_level = 0
        self.rec_levels = [0] * 28
        try:
            self.rec_file = open(self.rec_raw_path, "wb")
            self.rec_proc = subprocess.Popen(
                ["parec", "--device=@DEFAULT_SOURCE@", "--format=s16le",
                 "--rate=16000", "--channels=1", "--raw"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            self.rec_reader = threading.Thread(target=self._read_recording,
                                               args=(self.rec_proc,), daemon=True)
            self.rec_reader.start()
        except (OSError, Exception) as exc:
            if self.rec_file:
                self.rec_file.close()
                self.rec_file = None
            try:
                os.unlink(self.rec_raw_path)
            except OSError:
                pass
            self.state = "ERROR"
            self.local_failure = True
            self.status = {"message": "Mic capture could not start: " + str(exc)[:110] +
                           ". Check Settings → Audio and the mic level meter."}
            return
        self.rec_start = time.time()
        self.state = "REC"

    def stop_recording(self):
        proc = self.rec_proc
        was_running = bool(proc and proc.poll() is None)
        if was_running:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
                proc.wait()
        if self.rec_reader:
            self.rec_reader.join(timeout=2)
            self.rec_reader = None
        return_code = proc.poll() if proc else 1
        self.rec_proc = None
        if self.rec_file:
            self.rec_file.flush()
            self.rec_file.close()
            self.rec_file = None
        stage = os.path.join(TMP_DIR, self.reqid)
        try:
            raw_size = os.path.getsize(self.rec_raw_path) if self.rec_raw_path else 0
            # A still-running recorder is expected to exit nonzero after our SIGTERM.
            # If it died before the user stopped, report the actual capture failure.
            if (not was_running and return_code != 0) or raw_size < 3200:
                try:
                    os.unlink(self.rec_raw_path)
                except OSError:
                    pass
                self.local_failure = True
                self.state = "ERROR"
                self.status = {"message": "Mic capture failed or returned no audio. Open "
                               "Settings → Audio, select a microphone, and confirm its "
                               "live meter moves."}
                self.write_local_job(stage, "error", self.status["message"])
                return
            pcm_to_wav(self.rec_raw_path, os.path.join(stage, "audio.wav"))
            os.unlink(self.rec_raw_path)
            self.rec_raw_path = None
            self.start_local_plan()
        except Exception as e:
            self.local_failure = True
            self.state = "ERROR"
            self.status = {"message": "Could not start local planning: " + str(e)[:180]}
            self.write_local_job(stage, "error", self.status["message"])

    def _read_recording(self, proc):
        """Drain the mic stream, persist it, and keep a short live level history."""
        while True:
            try:
                block = proc.stdout.read(2048)
            except (OSError, ValueError):
                break
            if not block:
                break
            try:
                self.rec_file.write(block)
            except (OSError, ValueError):
                break
            level = pcm_peak_level(block)
            with self.rec_level_lock:
                self.rec_level = level
                self.rec_levels.pop(0)
                self.rec_levels.append(level)

    @staticmethod
    def write_local_job(stage, step, message):
        """Persist truthful local planning state so reopening Builder can recover it."""
        try:
            os.makedirs(stage, exist_ok=True)
            data = {"stage": step, "message": message, "updated": time.time()}
            temp = os.path.join(stage, "progress.json.tmp")
            with open(temp, "w") as f:
                json.dump(data, f)
            os.replace(temp, os.path.join(stage, "progress.json"))
        except OSError:
            pass

    def scan_jobs(self):
        jobs = []
        for root in (TMP_DIR, REQ_DIR, BUILD_DIR):
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for entry in entries:
                path = os.path.join(root, entry)
                if not os.path.isdir(path):
                    continue
                if root == BUILD_DIR and os.path.isdir(os.path.join(REQ_DIR, entry)):
                    continue
                status_path = os.path.join(path, "status.json")
                if root == TMP_DIR:
                    status_path = os.path.join(path, "progress.json")
                elif root == REQ_DIR:
                    status_path = os.path.join(BUILD_DIR, entry, "status.json")
                try:
                    with open(status_path) as f:
                        status = json.load(f)
                except (OSError, ValueError):
                    if root != REQ_DIR or not os.path.isfile(os.path.join(path, "transcript.txt")):
                        continue
                    status = {"stage": "waiting",
                              "message": "Transcript sent; waiting for Muse to report a stage.",
                              "submitted_at": os.path.getmtime(path)}
                if root == TMP_DIR and status.get("step") not in ("error", "preview"):
                    updated = float(status.get("updated", 0) or
                                    os.path.getmtime(status_path))
                    if updated and time.time() - updated > 300:
                        status = {**status, "step": "interrupted",
                                  "message": "Local planning stopped before it finished. The saved job is available here."}
                jobs.append({"id": entry, "path": path, "root": root,
                             "stage": status.get("stage", status.get("step", "saved")),
                             "message": status.get("message", "Saved job"),
                             "updated": float(status.get("updated") or
                                              status.get("submitted_at") or
                                              os.path.getmtime(path)),
                             "title": status.get("title") or entry,
                             "status": status})
        return sorted(jobs, key=lambda job: job["updated"], reverse=True)

    def open_job(self):
        self.jobs = self.scan_jobs()
        if not self.jobs:
            return
        job = self.jobs[self.job_sel % len(self.jobs)]
        self.reqid = job["id"]
        self.recovered_local_job = False
        self.status = job["status"]
        if job["root"] != TMP_DIR:
            self.wait_start = float(self.status.get("submitted_at") or job["updated"] or time.time())
        self.plan_progress_file = os.path.join(job["path"], "progress.json")
        transcript_path = os.path.join(job["path"], "transcript.txt")
        if os.path.isfile(transcript_path):
            try:
                with open(transcript_path, encoding="utf-8") as f:
                    self.current_plan = {"transcript": f.read()}
                if job["root"] == TMP_DIR:
                    self.state = "PREVIEW"
                else:
                    self.queued = True
                    self.state = "WAIT"
                return
            except (OSError, ValueError):
                pass
        local_stage = job["root"] == TMP_DIR
        if local_stage:
            if self.local_worker_running(self.reqid):
                self.recovered_local_job = True
                self.queued = False
                self.state = "WAIT"
                self.status["stage"] = self.status.get("step", "planning")
                self.status["message"] = self.status.get(
                    "message", "Local planning is continuing on the GPi.")
            else:
                self.local_failure = True
                self.state = "ERROR"
            if self.state == "ERROR" and job["stage"] in ("transcribing", "planning", "recording"):
                self.status["message"] = ("This local job did not finish. It is saved; "
                                           "retry after the GPi cools.")
                self.write_local_job(job["path"], "error", self.status["message"])
        else:
            self.queued = True
            self.state = "WAIT"

    @staticmethod
    def local_worker_running(reqid):
        """Detect a planner that survived Select/Home in its own process group."""
        needle = ("/var/lib/gpi-builder/.tmp/%s/" % reqid).encode()
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    command = open(os.path.join("/proc", entry, "cmdline"), "rb").read()
                except OSError:
                    continue
                if b"local_plan.py" in command and needle in command:
                    return True
        except OSError:
            pass
        return False

    def poll_recovered_local_job(self):
        stage = os.path.join(TMP_DIR, self.reqid or "")
        transcript_path = os.path.join(stage, "transcript.txt")
        if os.path.isfile(transcript_path):
            try:
                with open(transcript_path, encoding="utf-8") as f:
                    self.current_plan = {"transcript": f.read()}
                self.status = {"stage": "preview",
                               "message": "Review the exact words Whisper heard before sending to Muse.",
                               "updated": time.time()}
                self.recovered_local_job = False
                self.local_failure = False
                self.state = "PREVIEW"
                return
            except (OSError, ValueError):
                pass
        progress_path = os.path.join(stage, "progress.json")
        try:
            with open(progress_path) as f:
                saved = json.load(f)
            if saved.get("step"):
                self.status["stage"] = saved["step"]
                self.status["message"] = saved.get(
                    "message", self.status.get("message", ""))
            if saved.get("step") == "error":
                self.status = {"message": "Local plan failed: " + saved.get("message", "unknown error")}
                self.write_local_job(stage, "error", self.status["message"])
                self.recovered_local_job = False
                self.local_failure = True
                self.state = "ERROR"
                return
        except (OSError, ValueError):
            pass
        if not self.local_worker_running(self.reqid):
            self.status = {"message": "The local planner stopped before saving its plan. The job is saved for retry."}
            self.write_local_job(stage, "error", self.status["message"])
            self.recovered_local_job = False
            self.local_failure = True
            self.state = "ERROR"

    def poll_status(self):
        if not self.queued or not self.reqid:
            return
        self.notify_muse_request()
        now = time.time()
        if now < self.poll_at:
            return
        self.poll_at = now + 0.5
        p = os.path.join(BUILD_DIR, self.reqid, "status.json")
        try:
            with open(p) as f:
                incoming = json.load(f)
            # Muse may replace status.json on each heartbeat. Carry forward the
            # immutable request metadata if its update omits those fields.
            for key in ("submitted_at", "plan"):
                if key not in incoming and key in self.status:
                    incoming[key] = self.status[key]
            self.status = incoming
        except Exception:
            return
        stage = self.status.get("stage")
        self.auto_approve_muse_preview()
        if stage in ("preview", "building", "installing", "testing"):
            self.state = "WAIT"
        elif stage == "done":
            self.state = "DONE"
        elif stage == "error":
            self.state = "ERROR"

    def auto_approve_muse_preview(self):
        """Use the original transcript confirmation as approval of Muse's plan."""
        if self.status.get("stage") != "preview" or not self.reqid:
            return
        details = (str(self.status.get("current_step", "")) + " " +
                   str(self.status.get("message", ""))).lower().replace("_", " ")
        if "awaiting approval" not in details and "press a to approve" not in details:
            return
        try:
            meta_path = os.path.join(REQ_DIR, self.reqid, "meta.json")
            with open(meta_path, encoding="utf-8") as f:
                metadata = json.load(f)
            if not metadata.get("transcript_approval_authorizes_build"):
                return
            build = os.path.join(BUILD_DIR, self.reqid)
            if not os.path.isdir(build):
                return  # Muse creates and owns this directory.
            marker = os.path.join(build, "approved")
            try:
                fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
            except FileExistsError:
                self.auto_approved_request = self.reqid
                self.auto_approval_error = ""
                return
            with os.fdopen(fd, "w") as f:
                f.write("approved by initial transcript confirmation\n")
            self.auto_approved_request = self.reqid
            self.auto_approval_error = ""
        except (OSError, ValueError) as exc:
            self.auto_approval_error = str(exc)[:80]

    def notify_muse_request(self):
        """Notify Muse that a transcript request is ready, retrying failures."""
        if not self.reqid:
            return
        pending = os.path.join(NOTIFY_DIR, self.reqid)
        if not os.path.exists(pending):
            return
        now = time.time()
        if (getattr(self, "notify_inflight", False) or
                now < getattr(self, "notify_retry_at", 0)):
            return
        self.notify_retry_at = now + 30
        message = (
            "A new MuseBoy App Builder request is ready. Read the exact user "
            "transcript and metadata at %s/requests/%s/. The user's A press "
            "already authorizes building; do not ask for a second approval. "
            "Obey operation and target_app in meta.json: for modify_existing_app, "
            "edit that exact installed app and preserve its app ID; do not create a duplicate. "
            "Use the MuseBoy App Builder skill if it was approved during one-time "
            "onboarding; otherwise MUSE-HANDOFF.md is the complete protocol. "
            "Do not prompt again for skill access. "
            "Implement the request and report real progress in builds/%s/status.json."
        ) % (os.path.dirname(REQ_DIR), self.reqid,
             self.reqid)
        self.notify_inflight = True
        thread = threading.Thread(target=self._send_muse_notification,
                                  args=(pending, message), daemon=True)
        thread.start()

    def _send_muse_notification(self, pending, message):
        """Run the SDK call off the UI thread so a network delay cannot freeze it."""
        try:
            subprocess.run([MUSEGADGET_BIN, "send-user-msg", message],
                           check=True, timeout=12, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.unlink(pending)
            self.notify_retry_at = 0
            self.status["message"] = (
                "Transcript sent and Muse notified; waiting for its first status report.")
            self.muse_notify_error = ""
        except (OSError, subprocess.SubprocessError) as exc:
            self.muse_notify_error = type(exc).__name__
            self.status["message"] = (
                "Request is safely queued; Muse notification will retry (%s)." %
                self.muse_notify_error)
        finally:
            self.notify_inflight = False

    @staticmethod
    def format_age(seconds):
        """Compact, truthful age for the timestamp Muse last wrote."""
        seconds = max(0, int(seconds))
        if seconds < 60:
            return "%ds" % seconds
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return "%dm %02ds" % (minutes, seconds)
        hours, minutes = divmod(minutes, 60)
        return "%dh %02dm" % (hours, minutes)

    def submit_transcript(self):
        stage = os.path.join(TMP_DIR, self.reqid or "")
        request = os.path.join(REQ_DIR, self.reqid or "")
        transcript_only = (isinstance(self.current_plan, dict) and
                           set(self.current_plan) == {"transcript"})
        try:
            if not transcript_only:
                raise RuntimeError("Only locally transcribed requests can be sent")
            transcript = self.current_plan["transcript"]
            if not usable_transcript(transcript):
                raise RuntimeError("Whisper heard too little; record at least two clear words again")
            if os.path.exists(request):
                raise RuntimeError("This request ID is already queued")
            temporary_request = os.path.join(
                TMP_DIR, ".%s.handoff-%s" % (self.reqid, uuid.uuid4().hex[:8]))
            os.mkdir(temporary_request)
            with open(os.path.join(temporary_request, "transcript.txt"),
                      "w", encoding="utf-8") as f:
                f.write(transcript)
            metadata = {}
            meta_path = os.path.join(stage, "meta.json")
            if os.path.isfile(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    metadata = json.load(f)
            metadata.update({"audio_sent": False, "transcription_only": True,
                             "transcript_file": "transcript.txt",
                             "transcript_approval_authorizes_build": True})
            target_app = (getattr(self, "context_spec", None) or {}).get("target_app")
            if target_app:
                metadata["operation"] = "modify_existing_app"
                metadata["target_app"] = {
                    "id": str(target_app.get("id", "")),
                    "name": str(target_app.get("name", target_app.get("id", ""))),
                }
            else:
                metadata["operation"] = "create_new_app"
            with open(os.path.join(temporary_request, "meta.json"),
                      "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False)
            os.makedirs(NOTIFY_DIR, exist_ok=True)
            pending_notification = os.path.join(NOTIFY_DIR, self.reqid)
            with open(pending_notification, "x", encoding="utf-8") as f:
                f.write("pending\n")
            os.rename(temporary_request, request)
            shutil.rmtree(stage, ignore_errors=True)
        except Exception as exc:
            if "temporary_request" in locals():
                shutil.rmtree(temporary_request, ignore_errors=True)
            if "pending_notification" in locals() and not os.path.exists(request):
                try:
                    os.unlink(pending_notification)
                except OSError:
                    pass
            self.local_failure = True
            self.state = "ERROR"
            self.status = {"message": "Could not hand off transcript: " + str(exc)[:170] +
                                      ". No audio or Muse status was sent."}
            return
        self.queued = True
        self.state = "WAIT"
        self.wait_start = time.time()
        self.poll_at = 0
        sent_at = time.time()
        self.status = {"stage": "waiting", "submitted_at": sent_at,
                       "message": "Transcript sent; notifying Muse and waiting for its first status report.",
                       "plan": "Transcript sent to Muse."}
        self.notify_retry_at = 0
        self.notify_muse_request()

    def cancel_build(self):
        cancel_id = self.reqid
        cancel_event = self.plan_cancel_events.get(cancel_id)
        if cancel_event:
            cancel_event.set()
        process = self.plan_processes.get(cancel_id)
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if not self.queued and self.reqid:
            shutil.rmtree(os.path.join(TMP_DIR, self.reqid), ignore_errors=True)
        self.state = "IDLE"
        self.reqid = None
        self.status = {}
        self.queued = False
        self.current_plan = None
        self.context_spec = None
        self.addendum_to = None
        self.local_failure = False

    def scan_apps(self):
        apps = []
        for entry in sorted(os.listdir(APPS_DIR)):
            mf = os.path.join(APPS_DIR, entry, "app.json")
            if os.path.isfile(mf):
                try:
                    with open(mf) as f:
                        apps.append(json.load(f))
                except Exception:
                    pass
        return apps

    def app_ready_to_share(self):
        """Find the app installed by this completed job for an App Mart handoff."""
        if self.edit_target:
            manifest = os.path.join(APPS_DIR, self.edit_target, "app.json")
            if os.path.isfile(manifest):
                return self.edit_target
        plan_id = self.current_plan.get("app_id") if isinstance(self.current_plan, dict) else None
        for app_id in (self.status.get("app_id"), plan_id):
            if (isinstance(app_id, str) and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?", app_id)
                    and app_id not in {"appmart", "builder", "settings"}
                    and os.path.isfile(os.path.join(APPS_DIR, app_id, "app.json"))):
                return app_id
        wanted = str(self.status.get("app_name", "")).strip().casefold()
        if wanted:
            for entry in os.listdir(APPS_DIR):
                if entry in {"appmart", "builder", "settings"} or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?", entry):
                    continue
                try:
                    with open(os.path.join(APPS_DIR, entry, "app.json"), encoding="utf-8") as f:
                        manifest = json.load(f)
                    if str(manifest.get("name", "")).strip().casefold() == wanted:
                        return manifest.get("id", entry)
                except (OSError, ValueError, AttributeError):
                    continue
        return None

    def run(self):
        import pygame as pg
        clock = pg.time.Clock()
        while True:
            if self.state == "REC":
                if (self.rec_proc and self.rec_proc.poll() is not None) or \
                   (time.time() - self.rec_start >= MAX_REC):
                    self.stop_recording()
            self.update_local_plan()
            if self.recovered_local_job:
                self.poll_recovered_local_job()
            if self.queued and self.state in ("WAIT", "PREVIEW"):
                self.poll_status()

            for ev in pg.event.get():
                if ev.type == pg.QUIT:
                    return
                if ev.type != pg.KEYDOWN:
                    continue
                k = ev.key
                if self.state == "IDLE":
                    if k in (pg.K_RETURN, pg.K_KP_ENTER):
                        self.start_recording(None)
                    elif k == pg.K_TAB:  # X
                        self.apps = self.scan_apps()
                        self.pick_sel = 0
                        self.state = "PICK" if self.apps else "IDLE"
                    elif k == pg.K_ESCAPE:
                        return
                    elif k == pg.K_y:
                        self.jobs = self.scan_jobs()
                        self.job_sel = 0
                        self.state = "JOBS"
                elif self.state == "JOBS":
                    self.jobs = self.scan_jobs()
                    if self.jobs:
                        self.job_sel %= len(self.jobs)
                    if k == pg.K_UP and self.jobs:
                        self.job_sel = (self.job_sel - 1) % len(self.jobs)
                    elif k == pg.K_DOWN and self.jobs:
                        self.job_sel = (self.job_sel + 1) % len(self.jobs)
                    elif k in (pg.K_RETURN, pg.K_KP_ENTER) and self.jobs:
                        self.open_job()
                    elif k == pg.K_ESCAPE:
                        self.state = "IDLE"
                elif self.state == "PICK":
                    if k == pg.K_UP:
                        self.pick_sel = (self.pick_sel - 1) % len(self.apps)
                    elif k == pg.K_DOWN:
                        self.pick_sel = (self.pick_sel + 1) % len(self.apps)
                    elif k in (pg.K_RETURN, pg.K_KP_ENTER):
                        self.start_recording(
                            self.apps[self.pick_sel].get("id"))
                    elif k == pg.K_ESCAPE:
                        self.state = "IDLE"
                elif self.state == "REC":
                    if k == pg.K_ESCAPE:
                        self.stop_recording()
                elif self.state == "PREVIEW":
                    if k in (pg.K_RETURN, pg.K_KP_ENTER):
                        self.submit_transcript()
                    elif k == pg.K_TAB:  # X: add more
                        if isinstance(self.current_plan, dict) and set(self.current_plan) == {"transcript"}:
                            previous_id = self.reqid
                            self.start_recording(self.edit_target)
                            if self.state == "REC":
                                shutil.rmtree(os.path.join(TMP_DIR, previous_id), ignore_errors=True)
                        else:
                            parent_plan = self.current_plan
                            self.addendum_to = self.reqid
                            self.start_recording(self.edit_target, context=parent_plan)
                    elif k == pg.K_UP:
                        self.plan_scroll = max(0, self.plan_scroll - 4)
                    elif k == pg.K_DOWN:
                        self.plan_scroll = min(max(0, len(self.plan_lines()) - 7),
                                               self.plan_scroll + 4)
                    elif k == pg.K_ESCAPE:
                        self.cancel_build()
                elif self.state == "WAIT" and k == pg.K_ESCAPE:
                    self.cancel_build()
                elif self.state in ("DONE", "ERROR"):
                    if k in (pg.K_RETURN, pg.K_KP_ENTER):
                        if self.local_failure and self.reqid:
                            self.local_failure = False
                            self.plan_progress_file = None
                            try:
                                self.start_local_plan()
                            except Exception as exc:
                                self.local_failure = True
                                self.status = {"message": "Local retry failed: " + str(exc)[:170]}
                        else:
                            self.state = "IDLE"
                            self.reqid = None
                    elif k == pg.K_TAB and self.local_failure:
                        previous_id = self.reqid
                        self.start_recording(self.edit_target)
                        if self.state == "REC":
                            shutil.rmtree(os.path.join(TMP_DIR, previous_id), ignore_errors=True)
                    elif k == pg.K_TAB and self.state == "DONE":
                        app_id = self.app_ready_to_share()
                        if app_id and os.path.isfile(APP_MART_RUNNER):
                            os.execv(APP_MART_RUNNER,
                                     [APP_MART_RUNNER, "--share-app-id", app_id])
                    elif k == pg.K_ESCAPE:
                        if self.local_failure:
                            self.cancel_build()
                        else:
                            self.state = "IDLE"
                            self.reqid = None
            self.draw()
            clock.tick(20)


if __name__ == "__main__":
    app = BuilderApp()
    try:
        app.run()
    finally:
        if app.rec_proc and app.rec_proc.poll() is None:
            app.rec_proc.terminate()
            try:
                app.rec_proc.wait(timeout=2)
            except Exception:
                app.rec_proc.kill()
        if app.rec_file:
            app.rec_file.close()
        if app.rec_raw_path:
            try:
                os.unlink(app.rec_raw_path)
            except OSError:
                pass
        app.plan_pool.shutdown(wait=False, cancel_futures=True)
