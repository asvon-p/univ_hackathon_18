"""연결: 착샷 ↔ FASHN VTON v1.5 착샷 서버 (시아가 띄운 GPU 서버)  (상태: 실제 연결 ✅)

서버 문서: {TRYON_API_URL}/docs  — 모든 요청에 헤더 "x-api-key" 필요 (없거나 틀리면 401)

POST /tryon   multipart  person(사람 사진) garment(옷 사진) category(tops|bottoms|one-pieces)
              garment_photo_type(auto|model|flat-lay) num_timesteps(10~50, 기본 30) guidance_scale seed
  202 {"job_id", "status": "queued"|"running", "queue_position", ...}
GET  /jobs/{job_id}
  200 {"job_id", "status": "queued"|"running"|"done"|"failed", "queue_position", "elapsed_sec",
       "error", "result_url"}          404 {"detail": "job not found (expired or wrong id)"}
GET  /results/{job_id}.png  → 576x864 PNG
GET  /health  → {"status": "ok", "gpu", "queued", ...}  (키 없이)

- 옷 한 벌에 약 40초 (RTX 3060 Ti, 2026-11-07 측정). 한 번에 하나씩 처리하고 나머지는 줄을 섬
- 여러 벌은 결과 사진을 다음 사람 사진으로 넣어 차례로 입힘 (하의 → 상의 → 아우터)
- 나중에 C의 백엔드가 이 서버를 대신 불러 주면(POST /api/v1/tryon) 이 파일만 고치면 됨

프로세스 환경 변수: TRYON_API_URL, TRYON_API_KEY. 로컬 실행기가 자동으로 전달합니다.
"""

from __future__ import annotations

import logging
import os

import httpx

from . import ApiError

TRYON_API_URL = os.getenv("TRYON_API_URL", "").rstrip("/")
TRYON_API_KEY = os.getenv("TRYON_API_KEY", "")
TIMEOUT = httpx.Timeout(30.0)

# 옷장 종류 → 착샷 서버 category (아우터는 상의 위에 한 번 더 입힘)
CATEGORY = {"bottom": "bottoms", "top": "tops", "outerwear": "tops"}


def enabled() -> bool:
    """착샷 서버 주소·키가 설정돼 있는지."""
    return bool(TRYON_API_URL and TRYON_API_KEY)


# 서버를 켤 때 착샷이 켜졌는지 터미널에 알려 줌 (꺼져 있으면 코디 카드에 착샷 버튼이 안 보임)
_log = logging.getLogger("uvicorn.error")
if enabled():
    _log.info("착샷 켜짐: %s", TRYON_API_URL)
else:
    _log.warning(
        "착샷 꺼짐: TRYON_API_URL·TRYON_API_KEY가 설정되지 않았어요. "
        "로컬 GPU 실행: python -m scripts.frontend_preview --tryon"
    )


async def submit(
    person: tuple[bytes, str],
    garment: tuple[bytes, str],
    category: str,
    photo_type: str = "auto",
) -> dict:
    """착샷 작업 등록 → {"job_id", "status", "queue_position", ...}."""
    files = {
        "person": ("person" + _ext(person[1]), person[0], person[1]),
        "garment": ("garment" + _ext(garment[1]), garment[0], garment[1]),
    }
    data = {"category": category, "garment_photo_type": photo_type}
    res = await _send("POST", "/tryon", files=files, data=data)
    return res.json()


async def get_job(job_id: str) -> dict:
    return (await _send("GET", f"/jobs/{job_id}")).json()


async def get_result(job_id: str) -> bytes:
    return (await _send("GET", f"/results/{job_id}.png")).content


def _ext(content_type: str) -> str:
    return ".png" if content_type == "image/png" else ".jpg"


async def _send(method: str, path: str, **kwargs) -> httpx.Response:
    if not enabled():
        raise ApiError(503, "착샷 서버가 설정되지 않았어요. (TRYON_API_URL·KEY)", "TRYON_OFF")
    try:
        async with httpx.AsyncClient(base_url=TRYON_API_URL, timeout=TIMEOUT, trust_env=False) as c:
            res = await c.request(method, path, headers={"x-api-key": TRYON_API_KEY}, **kwargs)
    except httpx.HTTPError:
        raise ApiError(
            0,
            "착샷 서버에 연결할 수 없어요. 서버가 켜져 있는지 확인해 주세요.",
            "TRYON_UNREACHABLE",
        ) from None
    if res.status_code == 401:
        raise ApiError(401, "착샷 서버 키가 맞지 않아요. (TRYON_API_KEY)", "TRYON_KEY")
    if res.status_code == 404:
        raise ApiError(404, "착샷 작업을 찾을 수 없어요. 다시 시도해 주세요.")
    if res.is_error:
        raise ApiError(
            res.status_code,
            "착샷 서버가 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
            "TRYON_ERROR",
        )
    return res
