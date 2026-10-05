import importlib.util
from concurrent.futures import Future
from pathlib import Path
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


class LocalBuildCancelTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
