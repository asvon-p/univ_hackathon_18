"""사진을 저장하기 전 서버에서 표준 JPEG로 변환합니다."""

import asyncio

from . import USE_MOCK, ApiError, request_bytes


async def normalize_photo(token: str, filename: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    if USE_MOCK:
        from backend.app.core.errors import AppError
        from backend.app.services.images import normalize_image

        try:
            return await asyncio.to_thread(normalize_image, content), "image/jpeg"
        except AppError as exc:
            raise ApiError(exc.status, exc.detail, exc.code) from exc
    return await request_bytes(
        "POST",
        "/images/normalize",
        token=token,
        files={"file": (filename, content, content_type)},
    )
