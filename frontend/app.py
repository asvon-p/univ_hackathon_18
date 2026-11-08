"""팀원 D — 프론트엔드 화면 서버 (FastAPI + Jinja2) 진입점.

실행 (저장소 루트에서):
    uvicorn frontend.app:app --reload --port 3000
    → http://localhost:3000

파일 구성 (브랜치별)
- common.py               공통 화면 틀 (메뉴·디자인·한글 표시)
- onboarding_wardrobe.py  feat/onboarding-wardrobe-ui  로그인·회원가입·프로필·옷장·옷 등록
- recommendation_ui.py    feat/recommendation-ui       홈(오늘 날씨·오늘의 코디)·코디 추천
- avatar_preview.py       feat/avatar-2d-preview       착샷 (FASHN VTON)
- connect/                C 백엔드 연결 (기본 실제 모드, 명시적으로 Mock 선택 가능)
"""

from __future__ import annotations

from fastapi import FastAPI

from . import avatar_preview, onboarding_wardrobe, recommendation_ui

app = FastAPI(title="Fitjang Frontend", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(onboarding_wardrobe.router)
app.include_router(recommendation_ui.router)
app.include_router(avatar_preview.router)
