"""Small, testable inventory helpers for on-device GGUF model settings."""
from pathlib import Path
from urllib.request import Request, urlopen
import json


MODEL_DIR = Path("/opt/gpi/local-ai/models")
MODELS_URL = "http://127.0.0.1:8089/v1/models"
# The planner service reserves memory for the desktop, GPi UI, and audio stack.
MAX_MODEL_BYTES = 2_500_000_000


def discover_models(model_dir=MODEL_DIR, max_bytes=MAX_MODEL_BYTES):
    """Return valid local GGUF models; mark oversized ones as unavailable."""
    root = Path(model_dir)
    found = []
    try:
        candidates = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return found
    for path in candidates:
        if not path.is_file() or path.suffix.lower() != ".gguf":
            continue
        try:
            size = path.stat().st_size
            with path.open("rb") as model:
                valid = model.read(4) == b"GGUF"
        except OSError:
            continue
        if not valid:
            continue
        found.append({"id": path.stem, "path": str(path), "size": size,
                      "supported": size <= max_bytes})
    return found


def running_model(url=MODELS_URL, timeout=2):
    """Return the ID actually advertised by the running llama-server."""
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
        models = payload.get("data", [])
        return models[0].get("id") if models else None
    except Exception:
        return None


def configured_model(config_path="/etc/gpi/local-ai/model.env"):
    """Read the alias configured for the next start without shell evaluation."""
    try:
        for line in Path(config_path).read_text().splitlines():
            if line.startswith("MODEL_ALIAS="):
                return line.partition("=")[2].strip()
    except OSError:
        pass
    return None


def snapshot(model_dir=MODEL_DIR, url=MODELS_URL,
             config_path="/etc/gpi/local-ai/model.env"):
    loaded = running_model(url)
    return {"models": discover_models(model_dir), "loaded": loaded,
            "configured": configured_model(config_path),
            "running": loaded is not None}
