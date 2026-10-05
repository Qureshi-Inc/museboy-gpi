#!/usr/bin/env python3
"""App Builder starring Bolt, your builder bot.

Bolt listens to your idea, shows you his build plan, and gets to work.
A: record a new app idea (up to 20s, B stops early)
X: pick an installed app, then record the change you want
Select is global home (launcher).
"""
import json
import math
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pygame

sys.path.insert(0, "/opt/gpi/common")
from gpi_ui import W, H, BLACK, WHITE, DIM, DARK
from bolt import Bolt

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
MAX_REC = 20
LOCAL_PLAN = "/opt/gpi/apps/builder/local_plan.py"


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
        self.rec_start = 0
        self.status = {}
        self.approved_pending = False
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
        self.apps = []
        self.pick_sel = 0
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
        for d in (REQ_DIR, BUILD_DIR, TMP_DIR):
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
        if self.edit_target:
            target = next((app.get("name", self.edit_target) for app in self.apps
                           if app.get("id") == self.edit_target), self.edit_target)
            rows.append(f"CHANGE INSTALLED APP: {target}")
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
            rows.extend((f"DATA: {source.get('name', '?')}",
                         f"URL: {source.get('endpoint', '?')}",
                         f"PARAMS: {source.get('params', '?')}",
                         f"TIMEOUT: {source.get('timeout_seconds', '?')}s",
                         f"OFFLINE: {source.get('network_failure_behavior', '?')}"))
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
            self.footer_hints([("Select", "home")])

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
            # waveform-ish bars
            bx, bw = 250, 350
            pygame.draw.rect(s, PANEL, (bx, 230, bw, 120), border_radius=14)
            bars = 28
            for i in range(bars):
                hgt = 8 + abs(math.sin(time.time() * 6 + i * 0.7)) * 44
                if self.addendum_to:
                    col = CYAN
                else:
                    col = AMBER
                x = bx + 14 + i * ((bw - 28) / bars)
                pygame.draw.rect(s, col,
                                 (x, 290 - hgt / 2, (bw - 28) / bars - 4, hgt),
                                 border_radius=3)
            t = self.f_small.render("adding detail..." if self.addendum_to
                                    else "speak now, Bolt is listening",
                                    True, DIM)
            s.blit(t, t.get_rect(center=(bx + bw // 2, 380)))
            self.footer_hints([("B", "stop")])

        elif st == "PREVIEW":
            self.bolt.draw(s, 130, 240, "think", 0.95)
            speech_bubble(s, self.f_bubble, "Review the full plan. D-pad scrolls; B always goes back.",
                          pygame.Rect(230, 76, 380, 70), (150, 175))
            # blueprint card with the plan
            r = pygame.Rect(230, 160, 380, 230)
            rounded(s, r, PANEL, edge=AMBER)
            t = self.f_small.render("BUILD PLAN", True, AMBER)
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
            self.footer_hints([("A", "build it"), ("X", "add more"),
                               ("B", "cancel")])

        elif st == "WAIT":
            stage = self.status.get("stage", "queued")
            msg = self.status.get("message", "Waiting for Muse to report its next step.")
            mood = ("listen" if stage == "local-transcription" else
                    "think" if stage in ("local-plan", "planning", "testing", "validating") else
                    "build" if stage in ("building", "coding", "installing", "deploying") else
                    "idle")
            self.bolt.draw(s, 150, 250, mood, 1.0)
            bubble = {
                "local-transcription": "Transcribing on the GPi. Your audio stays here.",
                "local-plan": "Gemma is drafting the app contract locally.",
                "approved": "Your plan is approved; waiting for Muse to start.",
                "queued": "Your plan is queued for Muse.",
                "coding": "Muse reports it is writing the app.",
                "building": "Muse reports it is building the app.",
                "testing": "Muse reports it is testing the app.",
                "validating": "Muse reports it is checking the app.",
                "installing": "Muse reports it is installing the app.",
                "deploying": "Muse reports it is installing the app.",
            }.get(stage, "Waiting for Muse's next status report.")
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
            self.footer_hints([("B", "cancel"), ("Select", "home")])

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
            self.footer_hints([("Select", "home"), ("A", "build another")])

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
            hints = ([ ("A", "retry plan"), ("B", "back") ]
                     if self.local_failure else [("A", "try again"), ("Select", "home")])
            self.footer_hints(hints)

        pygame.display.flip()

    # ---- local transcription, contract drafting, and Muse handoff ----
    def create_local_plan(self, audio_path, context, progress_path,
                          request_id, cancel_event):
        command = [sys.executable, LOCAL_PLAN, str(audio_path),
                   "--progress-file", str(progress_path)]
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
            stdout, stderr = proc.communicate(timeout=220)
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
        self.status = {"stage": "local-transcription",
                       "message": "Audio is processed on this GPi only.",
                       "updated": time.time()}

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
            try:
                result = future.result()
                plan = result["plan"]
                stage = os.path.join(TMP_DIR, self.reqid)
                plan_path = os.path.join(stage, "plan.json")
                temporary = plan_path + ".tmp"
                with open(temporary, "w") as f:
                    json.dump(plan, f, ensure_ascii=False, indent=2)
                os.replace(temporary, plan_path)
                meta = {"created": time.time(), "edit_app_id": self.edit_target,
                        "addendum_to": self.addendum_to,
                        "spec_file": "plan.json", "audio_sent": False,
                        "planner": plan.get("planner"),
                        "planning_seconds": result.get("planning_seconds")}
                with open(os.path.join(stage, "meta.json"), "w") as f:
                    json.dump(meta, f)
                # The recording is only for local transcription. Never put it in
                # the request folder Muse can read.
                try:
                    os.unlink(os.path.join(stage, "audio.wav"))
                except FileNotFoundError:
                    pass
                self.current_plan = plan
                self.status = {
                    "stage": "preview",
                    "message": "Local plan ready. Review it, then approve or add a detail.",
                    "plan": plan.get("preview", ""),
                    "updated": time.time(),
                }
                self.plan_scroll = 0
                self.state = "PREVIEW"
                self.local_failure = False
                self.addendum_to = None
            except Exception as exc:
                self.local_failure = True
                self.state = "ERROR"
                self.status = {"message": ("Local plan failed: " + str(exc)[:145] +
                                           ". Audio stayed on this GPi; nothing was sent.")}
            self.plan_cancel_events.pop(finished_request_id, None)
        elif self.plan_future and self.plan_progress_file:
            try:
                with open(self.plan_progress_file) as f:
                    progress = json.load(f)
                self.status.update(progress)
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
        self.approved_pending = False
        self.local_failure = False
        self.queued = False
        self.reqid = "req-%d-%s" % (int(time.time()), uuid.uuid4().hex[:6])
        stage = os.path.join(TMP_DIR, self.reqid)
        os.makedirs(stage, exist_ok=True)
        wav = os.path.join(stage, "audio.wav")
        try:
            self.rec_proc = subprocess.Popen(
                ["arecord", "-q", "-D", "plughw:3,0", "-f", "S16_LE",
                 "-r", "16000", "-c", "1", "-t", "wav",
                 "-d", str(MAX_REC), wav],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self.state = "ERROR"
            self.status = {"message": "mic not available"}
            return
        self.rec_start = time.time()
        self.state = "REC"

    def stop_recording(self):
        if self.rec_proc and self.rec_proc.poll() is None:
            self.rec_proc.terminate()
            try:
                self.rec_proc.wait(timeout=2)
            except Exception:
                self.rec_proc.kill()
        self.rec_proc = None
        stage = os.path.join(TMP_DIR, self.reqid)
        try:
            self.start_local_plan()
        except Exception as e:
            self.local_failure = True
            self.state = "ERROR"
            self.status = {"message": "Could not start local planning: " + str(e)[:180]}

    def poll_status(self):
        if not self.queued or not self.reqid:
            return
        now = time.time()
        if now < self.poll_at:
            return
        self.poll_at = now + 2.0
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
        if self.approved_pending and stage in (None, "preview"):
            return  # approval written; don't flip back to the plan screen
        self.approved_pending = False
        if stage == "preview":
            self.state = "PREVIEW"
        elif stage in ("queued", "approved", "building", "coding", "planning",
                       "testing", "validating", "installing", "deploying"):
            self.state = "WAIT"
        elif stage == "done":
            self.state = "DONE"
        elif stage == "error":
            self.state = "ERROR"

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

    def approve_build(self):
        stage = os.path.join(TMP_DIR, self.reqid or "")
        request = os.path.join(REQ_DIR, self.reqid or "")
        build = os.path.join(BUILD_DIR, self.reqid or "")
        try:
            if not self.current_plan or not os.path.isfile(os.path.join(stage, "plan.json")):
                raise RuntimeError("The local plan is missing")
            if os.path.exists(request):
                raise RuntimeError("This request ID is already queued")
            os.makedirs(build, exist_ok=True)
            status = {
                "stage": "preview",
                "message": "Local contract ready; waiting for the GPi approval.",
                "plan": self.current_plan.get("preview", ""),
                "submitted_at": time.time(),
                "updated": time.time(),
            }
            temp_status = os.path.join(build, "status.json.tmp")
            with open(temp_status, "w") as f:
                json.dump(status, f, ensure_ascii=False)
            os.replace(temp_status, os.path.join(build, "status.json"))
            for name in os.listdir(stage):
                if name == "progress.json" or name.lower().endswith(
                        (".wav", ".mp3", ".m4a", ".flac")):
                    os.unlink(os.path.join(stage, name))
            with open(os.path.join(build, "approved"), "w") as f:
                f.write("ok")
            os.rename(stage, request)  # Muse sees only the text contract.
        except Exception as exc:
            self.local_failure = True
            self.state = "ERROR"
            self.status = {"message": "Could not hand off the plan: " + str(exc)[:170] +
                                      ". No audio was sent."}
            return
        self.approved_pending = True
        self.queued = True
        self.state = "WAIT"
        self.wait_start = time.time()
        self.poll_at = 0
        submitted_at = time.time()
        self.status = {"stage": "approved", "submitted_at": submitted_at,
                       "updated": submitted_at,
                       "message": "Approved plan sent to Muse. Waiting for its first progress report."}

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
        if self.queued:
            try:
                with open(os.path.join(BUILD_DIR, self.reqid, "cancelled"), "w") as f:
                    f.write("ok")
            except Exception:
                pass
        elif self.reqid:
            shutil.rmtree(os.path.join(TMP_DIR, self.reqid), ignore_errors=True)
        self.state = "IDLE"
        self.reqid = None
        self.status = {}
        self.approved_pending = False
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

    def run(self):
        import pygame as pg
        clock = pg.time.Clock()
        while True:
            if self.state == "REC":
                if (self.rec_proc and self.rec_proc.poll() is not None) or \
                   (time.time() - self.rec_start >= MAX_REC):
                    self.stop_recording()
            self.update_local_plan()
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
                        self.approve_build()
                    elif k == pg.K_TAB:  # X: add more
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
        app.plan_pool.shutdown(wait=False, cancel_futures=True)
