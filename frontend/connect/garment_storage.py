"""연결: 옷 등록·옷장·옷 사진 ↔ C의 feat/garment-storage  (상태: 일부만 백엔드에 있음 · Mock으로 동작)
           B의 feat/vision-extraction(속성 추출), feat/garment-segmentation(누끼)은 C의 백엔드를 거쳐서 들어옴

GET /garments, POST /garments 는 백엔드 Mock에 이미 있음 (backend/app/main.py). 나머지는 프론트 제안.

GET    /garments                  200 [GarmentAttributes, ...]   ← shared/contracts.py 형식
GET    /garments/{id}             200 GarmentAttributes / 404 GARMENT_NOT_FOUND
POST   /garments  multipart file  201 GarmentAttributes  ← 사진 저장 + B의 extract()로 채운 값
                                  413 파일 초과 / 415 형식 오류
       프론트가 보내는 사진은 항상 1080×1080 JPEG (1:1)
PATCH  /garments/{id}  JSON 일부   200 GarmentAttributes  ← 사용자가 확인·수정한 값
       {"name", "location", "category", "subcategory", "color", "pattern", "style_candidates", "thermal_level", "status"}
DELETE /garments/{id}             204                     ← 등록 취소·삭제
GET    /garments/{id}/image?kind=cutout|original   200 이미지 파일 / 404  ← 프론트 제안 (C 확인 필요)

⚠ 이름(name)·위치(location)는 contracts.py의 GarmentAttributes에 아직 없음 → 아래 Garment로 프론트에서만 확장.
   C에게 요청: GarmentAttributes(또는 옷 테이블)에 name: str | None, location: str | None 추가, PATCH로 저장.
   백엔드가 이 두 값을 돌려주기 전까지 실제 모드에서는 이름·위치가 저장되지 않음.

사진 보여주기: 로그인 토큰이 프론트 서버 쿠키에 있어서 브라우저가 백엔드에서 직접 못 받음
  → 브라우저는 프론트의 /garment-images/{id} 로 요청 → fetch_image()가 토큰을 붙여 백엔드에서 받아 넘겨줌
  누끼(cutout, B)가 아직 없으면 원본(original)으로, 둘 다 없으면 화면에서 옷 그림으로 대체
"""

from __future__ import annotations

import asyncio
import json
import uuid

from shared.contracts import GarmentAttributes


class Garment(GarmentAttributes):
    """GarmentAttributes + 사용자가 붙이는 이름·위치 (contracts.py에 추가되면 이 클래스는 지워도 됨)."""

    name: str | None = None  # 예: "유니클로 네이비 니트"
    location: str | None = None  # 예: "안방 옷장 2칸", "본가"


from . import REPO_ROOT, USE_MOCK, ApiError, request, request_bytes
from .auth_profile import DEMO_EMAIL, mock_email


async def list_garments(token: str) -> list[Garment]:
    if USE_MOCK:
        return list(_mock_closet(token))
    data = await request("GET", "/garments", token=token)
    return [Garment(**g) for g in data.get("items", [])]


async def get_garment(token: str, garment_id: str) -> Garment:
    if USE_MOCK:
        return _mock_find(token, garment_id)
    return Garment(**await request("GET", f"/garments/{garment_id}", token=token))


async def create_garment(
    token: str, filename: str, content: bytes, content_type: str
) -> Garment:
    """사진을 올리면 백엔드가 저장하고 Vision(B)으로 속성을 채워서 돌려줍니다."""
    if USE_MOCK:
        return await _mock_create(token, content, content_type)
    files = {"file": (filename, content, content_type)}
    return Garment(**await request("POST", "/garments", token=token, files=files))


async def update_garment(token: str, garment_id: str, fields: dict) -> Garment:
    if USE_MOCK:
        return _mock_update(token, garment_id, fields)
    return Garment(
        **await request("PATCH", f"/garments/{garment_id}", token=token, json=fields)
    )


async def delete_garment(token: str, garment_id: str) -> None:
    if USE_MOCK:
        _mock_delete(token, garment_id)
        return
    await request("DELETE", f"/garments/{garment_id}", token=token)


def image_url(garment: Garment) -> str | None:
    """화면에서 쓸 옷 사진 주소. None이면 화면이 옷 그림으로 대체."""
    if USE_MOCK:
        return (
            f"/garment-images/{garment.garment_id}"
            if garment.garment_id in _MOCK_IMAGES
            else None
        )
    return f"/garment-images/{garment.garment_id}" if garment.cutout_path else None


def may_have_photo(garment: Garment) -> bool:
    """사진(원본·누끼)이 있을 수 있는 옷인지. 실제 모드는 등록할 때 원본을 올리므로 일단 True."""
    if USE_MOCK:
        return garment.garment_id in _MOCK_IMAGES
    return True


async def fetch_image(
    token: str, garment_id: str, kinds: tuple[str, ...] = ("cutout", "original")
) -> tuple[bytes, str]:
    """옷 사진 (내용, content-type). kinds 순서대로 찾음 (기본: 누끼 → 원본). 없으면 ApiError(404).

    착샷(connect/tryon.py)에는 배경이 있는 원본이 나아서 ("original", "cutout")으로 부름.
    """
    if USE_MOCK:
        _mock_find(token, garment_id)  # 내 옷인지 확인 (아니면 404)
        if garment_id not in _MOCK_IMAGES:
            raise ApiError(404, "사진이 없어요.", "IMAGE_NOT_FOUND")
        return _MOCK_IMAGES[garment_id]
    for i, kind in enumerate(kinds):
        try:
            return await request_bytes(
                "GET",
                f"/garments/{garment_id}/image",
                token=token,
                params={"kind": kind},
            )
        except ApiError as e:
            if e.status != 404 or i == len(kinds) - 1:
                raise
    raise ApiError(404, "사진이 없어요.", "IMAGE_NOT_FOUND")


# ---- Mock: 데모 계정은 mocks/garments.json, 새 계정은 빈 옷장. 올린 사진은 메모리에 보관 ----
_MOCK_GARMENTS: dict[str, list[Garment]] = {}
_MOCK_IMAGES: dict[str, tuple[bytes, str]] = {}


def mock_closet(token: str) -> list[Garment]:
    """Mock 옷장 (추천 Mock에서도 사용)."""
    return _mock_closet(token)


def _mock_closet(token: str) -> list[Garment]:
    email = mock_email(token)
    if email not in _MOCK_GARMENTS:
        _MOCK_GARMENTS[email] = _load_mock_garments() if email == DEMO_EMAIL else []
    return _MOCK_GARMENTS[email]


def _mock_find(token: str, garment_id: str) -> Garment:
    for g in _mock_closet(token):
        if g.garment_id == garment_id:
            return g
    # 다른 사용자의 옷도 404 (DECISIONS: 존재 여부를 노출하지 않음)
    raise ApiError(404, "옷을 찾을 수 없어요.", "GARMENT_NOT_FOUND")


async def _mock_create(token: str, content: bytes, content_type: str) -> Garment:
    await asyncio.sleep(0.8)  # Vision 처리 시간 흉내
    garment = Garment(garment_id=str(uuid.uuid4()), category="top")
    _mock_closet(token).insert(0, garment)
    _MOCK_IMAGES[garment.garment_id] = (content, content_type)
    return garment


def _mock_update(token: str, garment_id: str, fields: dict) -> Garment:
    closet = _mock_closet(token)
    old = _mock_find(token, garment_id)
    new = old.model_copy(update=fields)
    closet[closet.index(old)] = new
    return new


def _mock_delete(token: str, garment_id: str) -> None:
    _mock_closet(token).remove(_mock_find(token, garment_id))
    _MOCK_IMAGES.pop(garment_id, None)


def _load_mock_garments() -> list[Garment]:
    path = REPO_ROOT / "mocks" / "garments.json"
    try:
        return [Garment(**g) for g in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError):
        return []
