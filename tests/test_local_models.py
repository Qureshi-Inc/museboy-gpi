import json
from pathlib import Path
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from unittest.mock import patch

from local_models import configured_model, discover_models, running_model


class LocalModelTests(unittest.TestCase):
    def test_discovers_only_valid_gguf_and_marks_oversized_models(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "small.gguf").write_bytes(b"GGUF" + b"x" * 8)
            (root / "large.gguf").write_bytes(b"GGUF" + b"x" * 20)
            (root / "partial.gguf.part").write_bytes(b"GGUF" + b"x" * 8)
            (root / "wrong.gguf").write_bytes(b"NOPE" + b"x" * 8)
            models = discover_models(root, max_bytes=15)
            self.assertEqual([m["id"] for m in models], ["large", "small"])
            self.assertFalse(models[0]["supported"])
            self.assertTrue(models[1]["supported"])

    def test_configured_model_reads_only_plain_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "model.env"
            config.write_text("MODEL_PATH=/tmp/a.gguf\nMODEL_ALIAS=Gemma-1B\n")
            self.assertEqual(configured_model(config), "Gemma-1B")

    def test_running_model_reads_the_server_model_id(self):
        response = type("Response", (), {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
            "read": lambda self: json.dumps({"data": [{"id": "Gemma-1B"}]}).encode(),
        })()
        with patch("local_models.urlopen", return_value=response):
            self.assertEqual(running_model("http://127.0.0.1/models"), "Gemma-1B")

    def test_running_model_reports_offline_as_none(self):
        with patch("local_models.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(running_model("http://127.0.0.1/models"))


if __name__ == "__main__":
    unittest.main()
