"""팀원 D — 프론트엔드 화면 서버 (FastAPI + Jinja2) 진입점.

실행 (저장소 루트에서):
    uvicorn frontend.app:app --reload --port 3000
    → http://localhost:3000

파일 구성 (브랜치별)
- common.py               공통 화면 틀 (메뉴·디자인·한글 표시)
- onboarding_wardrobe.py  feat/onboarding-wardrobe-ui  로그인·회원가입·프로필·옷장·옷 등록
- recommendation_ui.py    feat/recommendation-ui       코디 추천 (자리만)
- avatar_preview.py       feat/avatar-2d-preview       2D 아바타 (자리만)
- connect/                다른 팀원 작업과 연결되는 부분 (아직 미완성, Mock으로 동작)
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from . import avatar_preview, onboarding_wardrobe, recommendation_ui
from .common import current_user, go_login

app = FastAPI(title="Fitjang Frontend", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(onboarding_wardrobe.router)
app.include_router(recommendation_ui.router)
app.include_router(avatar_preview.router)


@app.get("/")
async def home(request: Request):
    # TODO(D): 홈(날씨·오늘의 코디) 화면을 만들기 전까지는 옷장으로 보냄
    if current_user(request) is None:
        return go_login(request)
    return RedirectResponse("/wardrobe", status_code=303)
