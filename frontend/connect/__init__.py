"""팀원 D 화면과 C 백엔드 사이의 HTTP 연결 계층.

화면 파일(onboarding_wardrobe.py 등)은 백엔드를 직접 부르지 않고, 이 폴더의 함수만 호출합니다.
기본은 실제 백엔드 연결입니다. FRONTEND_USE_MOCK=true일 때만 화면용 Mock을 사용합니다.
전신 사진과 착샷은 별도 기능이며 기본적으로 비활성화합니다.

| 파일                  | 연결 대상 (브랜치 · 담당)                                   | 상태        |
|-----------------------|-------------------------------------------------------------|-------------|
| auth_profile.py       | 가입·로그인·동의 기반 프로필 · C                               | 연결 완료 |
| garment_storage.py    | 의류 등록·속성 수정·이미지 · C (+ B 비전)                       | 연결 완료 |
| recommendation.py     | 추천·피드백 · C (+ A 추천)                                    | 연결 완료 |
| weather.py            | C의 Open-Meteo 어댑터                                        | 연결 완료 |
| tryon.py              | 착샷 서버 (FASHN VTON v1.5, 시아가 띄운 GPU 서버)            | 실제 연결 ✅ |

환경 변수 (frontend/.env 파일에 적어 두면 자동으로 읽음 — .env는 커밋 금지, .env.example 참고)
- BACKEND_API_URL   : 백엔드 주소 (기본 http://localhost:8000/api/v1)
- FRONTEND_USE_MOCK : "true"일 때만 Mock (기본 false)
- FITJANG_SKIP_DOTENV : "1"이면 frontend/.env 파일 접근 자체를 생략
- FRONTEND_BODY_PHOTO_ENABLED : 기본 false, 전신 사진 API 통합 후에만 활성화
- TRYON_API_URL, TRYON_API_KEY : 착샷 서버 주소·키 (connect/tryon.py)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

# 팀 공통 데이터 계약(shared/contracts.py)을 그대로 씁니다
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    """frontend/.env 의 KEY=VALUE 를 환경 변수로 (이미 설정된 값이 우선). 라이브러리 없이."""
    if os.getenv("FITJANG_SKIP_DOTENV") == "1":
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).resolve().parents[1] / ".env")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://localhost:8000/api/v1").rstrip("/")
USE_MOCK = os.getenv("FRONTEND_USE_MOCK", "false").lower() == "true"
BODY_PHOTO_ENABLED = os.getenv("FRONTEND_BODY_PHOTO_ENABLED", "false").lower() == "true"
TIMEOUT = httpx.Timeout(35.0, connect=5.0)


class ApiError(Exception):
    """백엔드 에러를 하나의 형태로 맞춥니다.

    - 팀 규칙: {"detail": "메시지", "code": "GARMENT_NOT_FOUND"}
    - FastAPI 기본 422: {"detail": [{"loc": [...], "msg": "...", "type": "..."}]}
    - status 0 = 서버에 연결하지 못함
    """

    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


async def request(method: str, path: str, *, token: str | None = None, **kwargs) -> dict:
    """JSON API 호출. 목록 응답은 {"items": [...]}로 감싸서 돌려줌."""
    res = await _send(method, path, token=token, **kwargs)
    if res.status_code == 204:
        return {}
    try:
        data = res.json()
    except ValueError:
        data = None
    if res.is_error:
        raise _to_api_error(res.status_code, data)
    return data if isinstance(data, dict) else {"items": data}


async def request_bytes(method: str, path: str, *, token: str | None = None, **kwargs) -> tuple[bytes, str]:
    """이미지 같은 파일 받기 → (내용, content-type)."""
    res = await _send(method, path, token=token, **kwargs)
    if res.is_error:
        try:
            data = res.json()
        except ValueError:
            data = None
        raise _to_api_error(res.status_code, data)
    return res.content, res.headers.get("content-type", "application/octet-stream")


async def _send(method: str, path: str, *, token: str | None, **kwargs) -> httpx.Response:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(base_url=BACKEND_API_URL, timeout=TIMEOUT) as client:
            return await client.request(method, path, headers=headers, **kwargs)
    except httpx.HTTPError:
        raise ApiError(0, "서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.", "NETWORK_ERROR") from None


def _to_api_error(status: int, data: object) -> ApiError:
    fallback = _DEFAULT_MESSAGES.get(status, "알 수 없는 오류가 발생했어요. 잠시 후 다시 시도해 주세요.")
    if isinstance(data, dict):
        detail, code = data.get("detail"), data.get("code")
        if code is None and status in (404, 405) and detail in ("Not Found", "Method Not Allowed"):
            # FastAPI 기본 응답 = 백엔드에 아직 그 API가 없음 (연결 전)
            return ApiError(
                status,
                "서버에 아직 이 기능이 없어요. (백엔드 연결 전)",
                "API_NOT_READY",
            )
        if isinstance(detail, str) and detail:
            return ApiError(status, detail, code)
        if isinstance(detail, list):
            return ApiError(status, fallback, code or "VALIDATION_ERROR")
    return ApiError(status, fallback)


_DEFAULT_MESSAGES = {
    400: "요청이 올바르지 않아요.",
    401: "로그인이 필요해요.",
    403: "접근 권한이 없어요.",
    404: "찾을 수 없어요.",
    413: "파일이 너무 커요. 10MB 이하로 올려 주세요.",
    415: "JPG, PNG, HEIC 또는 HEIF 사진을 올려 주세요.",
    422: "입력값을 다시 확인해 주세요.",
}
