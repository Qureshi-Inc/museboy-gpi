import importlib.util
from concurrent.futures import Future
from pathlib import Path
import tempfile
import json
import sys
import threading
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
pygame_stub = types.ModuleType("pygame")
gpi_ui_stub = types.ModuleType("gpi_ui")
for name in ("GlobalKeyWatcher", "W", "H", "BLACK", "WHITE", "DIM", "DARK"):
    setattr(gpi_ui_stub, name, object())
bolt_stub = types.ModuleType("bolt")
bolt_stub.Bolt = object
sys.modules.setdefault("pygame", pygame_stub)
sys.modules.setdefault("gpi_ui", gpi_ui_stub)
sys.modules.setdefault("bolt", bolt_stub)
spec = importlib.util.spec_from_file_location(
    "museboy_builder", ROOT / "apps" / "builder" / "builder.py")
builder_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder_module)


class InlineThread:
    """Run a thread target inline so the asynchronous hook is deterministic."""
    def __init__(self, target, args=(), daemon=None):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


class LocalBuildCancelTests(unittest.TestCase):
    def test_live_mic_meter_reads_real_pcm_and_reports_silence_as_zero(self):
        silence = b"\x00\x00" * 512
        voice = b"\x00\x00\x00\x10" * 512
        self.assertEqual(builder_module.pcm_peak_level(silence), 0)
        self.assertGreater(builder_module.pcm_peak_level(voice), 0)

    def test_pcm_capture_converts_to_valid_mono_16khz_wav(self):
        import wave
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp, "audio.pcm")
            wav = Path(tmp, "audio.wav")
            pcm = b"\x01\x00\x02\x00" * 400
            raw.write_bytes(pcm)
            builder_module.pcm_to_wav(str(raw), str(wav))
            with wave.open(str(wav), "rb") as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertEqual(audio.getframerate(), 16000)
                self.assertEqual(audio.readframes(audio.getnframes()), pcm)

    def test_transcript_handoff_writes_two_request_files_and_never_build_files(self):
        builder = builder_module.BuilderApp.__new__(builder_module.BuilderApp)
        builder.reqid = "req-contract-test"
        builder.current_plan = {"transcript": "Make a tiny weather app"}
        builder.local_failure = False
        builder.state = "PREVIEW"
        builder.queued = False
        builder.plan_cancel_events = {}
        builder.plan_processes = {}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp, "tmp")
            requests = Path(tmp, "requests")
            builds = Path(tmp, "builds")
            notifications = Path(tmp, "notify-pending")
            stage = tmp_root / builder.reqid
            stage.mkdir(parents=True)
            requests.mkdir()
            builds.mkdir()
            (stage / "audio.wav").write_bytes(b"private audio")
            (stage / "meta.json").write_text(json.dumps({
                "progress_protocol": {"stage_values": [
                    "preview", "building", "installing", "testing", "done", "error"]}}))
            with patch.object(builder_module, "TMP_DIR", str(tmp_root)), \
                    patch.object(builder_module, "REQ_DIR", str(requests)), \
                    patch.object(builder_module, "BUILD_DIR", str(builds)), \
                    patch.object(builder_module, "NOTIFY_DIR", str(notifications)), \
                    patch.object(builder_module.threading, "Thread", InlineThread), \
                    patch.object(builder_module.subprocess, "run") as send_user_msg:
                builder.submit_transcript()
            request = requests / builder.reqid
            self.assertEqual({p.name for p in request.iterdir()},
                             {"transcript.txt", "meta.json"})
            self.assertEqual((request / "transcript.txt").read_text(),
                             "Make a tiny weather app")
            metadata = json.loads((request / "meta.json").read_text())
            self.assertFalse(metadata["audio_sent"])
            self.assertTrue(metadata["transcript_approval_authorizes_build"])
            self.assertEqual(metadata["operation"], "create_new_app")
            self.assertEqual(metadata["progress_protocol"]["stage_values"],
                             ["preview", "building", "installing", "testing", "done", "error"])
            self.assertEqual(list(builds.iterdir()), [])
            self.assertFalse((stage / "audio.wav").exists())
            self.assertEqual({p.name for p in request.iterdir()},
                             {"transcript.txt", "meta.json"})
            send_user_msg.assert_called_once()
            command = send_user_msg.call_args.args[0]
            self.assertEqual(command[:2], [builder_module.MUSEGADGET_BIN,
                                           "send-user-msg"])
            self.assertIn(builder.reqid, command[2])
            self.assertNotIn("Make a tiny weather app", command[2])
            self.assertFalse((notifications / builder.reqid).exists())

    def test_edit_handoff_names_existing_target_in_metadata(self):
        builder = builder_module.BuilderApp.__new__(builder_module.BuilderApp)
        builder.reqid = "req-edit-contract"
        builder.current_plan = {"transcript": "Add a score counter"}
        builder.local_failure = False
        builder.state = "PREVIEW"
        builder.queued = False
        builder.plan_cancel_events = {}
        builder.plan_processes = {}
        builder.edit_target = "tetris"
        builder.context_spec = {"target_app": {"id": "tetris", "name": "Tetris"}}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp, "tmp")
            requests = Path(tmp, "requests")
            builds = Path(tmp, "builds")
            notifications = Path(tmp, "notify-pending")
            stage = tmp_root / builder.reqid
            stage.mkdir(parents=True)
            requests.mkdir()
            builds.mkdir()
            with patch.object(builder_module, "TMP_DIR", str(tmp_root)), \
                    patch.object(builder_module, "REQ_DIR", str(requests)), \
                    patch.object(builder_module, "BUILD_DIR", str(builds)), \
                    patch.object(builder_module, "NOTIFY_DIR", str(notifications)), \
                    patch.object(builder_module.threading, "Thread", InlineThread), \
                    patch.object(builder_module.subprocess, "run"):
                builder.submit_transcript()
            metadata = json.loads((requests / builder.reqid / "meta.json").read_text())
            self.assertEqual(metadata["operation"], "modify_existing_app")
            self.assertEqual(metadata["target_app"], {"id": "tetris", "name": "Tetris"})

    def test_muse_notification_failure_keeps_request_queued_and_retries(self):
        builder = builder_module.BuilderApp.__new__(builder_module.BuilderApp)
        builder.reqid = "req-notify-retry"
        builder.status = {"stage": "waiting", "message": "Sending"}
        builder.notify_retry_at = 0
        with tempfile.TemporaryDirectory() as tmp:
            notifications = Path(tmp, "notify-pending")
            notifications.mkdir()
            pending = notifications / builder.reqid
            pending.write_text("pending\n")
            with patch.object(builder_module, "NOTIFY_DIR", str(notifications)), \
                    patch.object(builder_module, "REQ_DIR", "/var/lib/gpi-builder/requests"), \
                    patch.object(builder_module, "LOCAL_PLAN", "/opt/gpi/apps/builder/local_plan.py"), \
                    patch.object(builder_module.threading, "Thread", InlineThread), \
                    patch.object(builder_module.subprocess, "run",
                                 side_effect=FileNotFoundError("not installed")):
                builder.notify_muse_request()
            self.assertTrue(pending.exists())
            self.assertIn("safely queued", builder.status["message"])
            builder.notify_retry_at = 0
            with patch.object(builder_module, "NOTIFY_DIR", str(notifications)), \
                    patch.object(builder_module, "REQ_DIR", "/var/lib/gpi-builder/requests"), \
                    patch.object(builder_module, "LOCAL_PLAN", "/opt/gpi/apps/builder/local_plan.py"), \
                    patch.object(builder_module.threading, "Thread", InlineThread), \
                    patch.object(builder_module.subprocess, "run") as send_user_msg:
                builder.notify_muse_request()
            send_user_msg.assert_called_once()
            self.assertFalse(pending.exists())
            self.assertIn("Muse notified", builder.status["message"])

    def test_auto_approval_writes_only_marker_after_muse_preview(self):
        builder = builder_module.BuilderApp.__new__(builder_module.BuilderApp)
        builder.reqid = "req-auto-approve"
        builder.status = {"stage": "preview", "current_step": "awaiting_approval",
                          "message": "Build plan ready — press A to approve."}
        builder.auto_approved_request = None
        builder.auto_approval_error = ""
        with tempfile.TemporaryDirectory() as tmp:
            requests = Path(tmp, "requests")
            builds = Path(tmp, "builds")
            request = requests / builder.reqid
            build = builds / builder.reqid
            request.mkdir(parents=True)
            build.mkdir(parents=True)
            (request / "meta.json").write_text(json.dumps({
                "transcript_approval_authorizes_build": True}))
            status_path = build / "status.json"
            status_path.write_text(json.dumps(builder.status))
            before = status_path.read_bytes()
            with patch.object(builder_module, "REQ_DIR", str(requests)), \
                    patch.object(builder_module, "BUILD_DIR", str(builds)):
                builder.auto_approve_muse_preview()
                builder.auto_approve_muse_preview()
            self.assertTrue((build / "approved").exists())
            self.assertEqual(status_path.read_bytes(), before)
            self.assertEqual({p.name for p in build.iterdir()},
                             {"status.json", "approved"})
            self.assertEqual(builder.auto_approved_request, builder.reqid)

    def test_cancel_terminates_local_worker_and_clears_temporary_request(self):
        builder = builder_module.BuilderApp.__new__(builder_module.BuilderApp)
        cancel = threading.Event()
        process = types.SimpleNamespace(pid=123, poll=lambda: None)
        builder.reqid = "req-example"
        builder.plan_cancel_events = {builder.reqid: cancel}
        builder.plan_processes = {builder.reqid: process}
        builder.queued = False
        builder.state = "WAIT"
        builder.status = {"stage": "planning"}
        builder.approved_pending = False
        builder.current_plan = {"title": "draft"}
        builder.context_spec = {"title": "draft"}
        builder.addendum_to = None
        builder.local_failure = False

        with patch.object(builder_module.os, "killpg") as killpg, \
                patch.object(builder_module.shutil, "rmtree") as rmtree:
            builder.cancel_build()

        self.assertTrue(cancel.is_set())
        killpg.assert_called_once_with(process.pid, builder_module.signal.SIGTERM)
        rmtree.assert_called_once_with(
            builder_module.os.path.join(builder_module.TMP_DIR, "req-example"),
            ignore_errors=True)
        self.assertIsNone(builder.reqid)
        self.assertEqual(builder.state, "IDLE")

    def test_cancelled_worker_completion_is_discarded_without_reopening_error(self):
        builder = builder_module.BuilderApp.__new__(builder_module.BuilderApp)
        future = Future()
        future.set_exception(RuntimeError("cancelled"))
        builder.plan_future = future
        builder.plan_request_id = "req-old"
        builder.reqid = None
        builder.plan_cancel_events = {"req-old": threading.Event()}
        builder.update_local_plan()
        self.assertIsNone(builder.plan_future)
        self.assertIsNone(builder.plan_request_id)
        self.assertNotIn("req-old", builder.plan_cancel_events)

    def test_failed_plan_is_saved_without_crashing_builder(self):
        builder = builder_module.BuilderApp.__new__(builder_module.BuilderApp)
        future = Future()
        future.set_exception(RuntimeError("planner timed out"))
        builder.plan_future = future
        builder.plan_request_id = "req-failed"
        builder.reqid = "req-failed"
        builder.plan_cancel_events = {"req-failed": threading.Event()}
        builder.local_failure = False
        builder.edit_target = None
        builder.addendum_to = None
        builder.plan_scroll = 0
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(builder_module, "TMP_DIR", tmp):
            builder.update_local_plan()
            self.assertEqual(builder.state, "ERROR")
            self.assertIn("timed out", builder.status["message"])
            progress = Path(tmp, "req-failed", "progress.json")
            self.assertEqual(__import__("json").loads(progress.read_text())["stage"],
                             "error")


if __name__ == "__main__":
    unittest.main()
