"""Non-secret, live maintenance state shared by the UI and GPU worker."""

import json
import os
import time
from pathlib import Path

DEFAULT_STATE = Path(__file__).resolve().parents[1] / ".demo/tryon-runtime/maintenance.json"
MESSAGES = {
    "draining": "착샷을 잠시 중지할 준비 중이에요. 진행 중인 착샷이 끝나면 일시 중지됩니다.",
    "paused": "모델 학습으로 착샷 생성이 일시 중지되었어요. 학습이 끝나면 다시 이용할 수 있어요.",
    "starting": "착샷 서비스를 다시 준비하고 있어요. 잠시 후 이용해 주세요.",
    "error": "착샷 서비스를 준비하지 못했어요. 관리자 확인 후 다시 이용할 수 있어요.",
}


def state_path() -> Path:
    return Path(os.environ.get("TRYON_MAINTENANCE_FILE", str(DEFAULT_STATE)))


def read_state(path: Path | None = None) -> dict:
    try:
        state = json.loads((path or state_path()).read_text(encoding="utf-8"))
        phase = state["phase"]
        if phase not in {*MESSAGES, "ready"}:
            raise ValueError("Invalid maintenance phase")
    except FileNotFoundError:
        phase = "ready"
    except (OSError, ValueError, KeyError, TypeError):
        phase = "error"  # A corrupt state must not silently enable GPU work.
    return {"phase": phase, "available": phase == "ready", "message": MESSAGES.get(phase, "")}


def write_state(path: Path, phase: str) -> None:
    if phase not in {*MESSAGES, "ready"}:
        raise ValueError("Invalid maintenance phase")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"phase": phase}), encoding="utf-8")
        for attempt in range(25):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows readers/antivirus can briefly hold a non-delete-sharing handle.
                if attempt == 24:
                    raise
                time.sleep(0.04)
    finally:
        temporary.unlink(missing_ok=True)
