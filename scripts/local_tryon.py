"""Local GPU worker. Never loads .env; launched by frontend_preview --tryon."""

import json
import os
import secrets
import sys
from pathlib import Path

from scripts.frontend_preview import ROOT, deny_dotenv

RUNTIME = ROOT / ".demo" / "tryon-runtime"
CREDENTIALS = RUNTIME / "credentials.json"
PORT = int(os.environ.get("TRYON_PORT", "8010"))


def runtime_environment(root: Path = ROOT) -> tuple[Path, dict[str, str]]:
    runtime = root / ".demo" / "tryon-runtime"
    credentials = runtime / "credentials.json"
    python = runtime / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file() or not (runtime / "weights/model.safetensors").is_file():
        raise RuntimeError("GPU runtime is not installed; see docs/LOCAL_TRYON.md")
    if not credentials.exists():
        with credentials.open("x", encoding="utf-8") as stream:
            json.dump({"api_key": secrets.token_urlsafe(48)}, stream)
    key = json.loads(credentials.read_text(encoding="utf-8"))["api_key"]
    if not isinstance(key, str) or len(key) < 32:
        raise RuntimeError("Invalid GPU credentials; existing credentials were not overwritten")
    return python, {
        "FITJANG_SKIP_DOTENV": "1",
        "FRONTEND_BODY_PHOTO_ENABLED": "true",
        "TRYON_API_URL": f"http://127.0.0.1:{PORT}",
        "TRYON_PORT": str(PORT),
        "TRYON_API_KEY": key,
        "TRYON_MAINTENANCE_FILE": str(runtime / "maintenance.json"),
        "TRYON_TIMING_FILE": str(runtime / "timing-v1.json"),
        "FASHN_WEIGHTS_DIR": str(runtime / "weights"),
        "FASHN_OUTPUT_DIR": str(runtime / "outputs"),
        "HF_HOME": str(runtime / "huggingface"),
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    if not os.environ.get("TRYON_API_KEY"):
        raise RuntimeError("Start with python -m scripts.frontend_preview --tryon")
    import uvicorn

    uvicorn.run("tryon_server.server:app", host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
