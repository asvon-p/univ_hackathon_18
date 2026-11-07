"""feat/avatar-2d-preview (D) — 2D 아바타 위에 옷 누끼 이미지 겹쳐 보기.  (상태: 아직 만들기 전, 자리만)

필요한 연결 (connect/ 폴더):
- garment_storage.image_url() / fetch_image()  ← 누끼 PNG는 B feat/garment-segmentation 결과 (C 백엔드 경유)
- auth_profile.get_profile()                   ← 키·몸무게로 아바타 비율 조정 (선택)

TODO(D): 아바타 그림, 신발 → 하의 → 상의 → 아우터 순서로 겹치기, 누끼가 없으면 garment_art 그림으로 대체
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .common import current_user, go_login, render

router = APIRouter()


@router.get("/avatar", response_class=HTMLResponse)
async def avatar_page(request: Request):
    if current_user(request) is None:
        return go_login(request)
    return render(
        request,
        "placeholder.html",
        active="recommend",
        title="2D 아바타 미리보기",
        message="아바타 미리보기는 다음 작업에서 만들어요.",
        back="/recommend",
    )
