import sys

from fastapi import APIRouter

from backend.app.adapters.registry import get_recommender, get_vision, get_weather
from backend.app.core.config import get_settings
from backend.app.db.session import check_db
from backend.app.schemas.health import HealthResponse
from shared.contracts import CONTRACT_VERSION

router = APIRouter(tags=["health"])


def _gpu_status() -> str | None:
    torch = sys.modules.get("torch")  # torch 를 새로 import 하지 않음 (느림). 비전 모델이 로딩했을 때만 표시
    if torch is None:
        return None
    try:
        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "unknown"


@router.get("/health", response_model=HealthResponse)
def health():
    ok, err = check_db()
    return HealthResponse(
        status="ok" if ok else "degraded",
        app_env=get_settings().APP_ENV,
        contract_version=CONTRACT_VERSION,
        db="ok" if ok else "down",
        db_error=err,
        adapters={
            "vision": get_vision().name,
            "recommender": get_recommender().name,
            "weather": get_weather().name,
        },
        gpu=_gpu_status(),
    )
