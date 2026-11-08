"""FastAPI 진입점 (팀원 C).

실행 (저장소 루트에서): .\\scripts\\dev.ps1 run   또는   uvicorn backend.app.main:app --reload
API 문서: http://localhost:8000/docs
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.adapters import registry
from backend.app.api.v1 import auth, garments, health, media, profile, recommendations, weather
from backend.app.core.config import get_settings
from backend.app.core.errors import register_exception_handlers
from backend.app.core.logging import setup_logging
from backend.app.storage.worker import retry_deletions
from shared.contracts import CONTRACT_VERSION

log = logging.getLogger("fitjang")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    # A·B 코드는 os.environ 으로 키를 읽습니다 (.env → 환경변수로 전달)
    for key in ("LLM_API_KEY", "WEATHER_API_KEY"):
        if getattr(s, key) and not os.environ.get(key):
            os.environ[key] = getattr(s, key)
    registry.warmup_all()
    log.info(
        "started env=%s vision=%s recommender=%s contract=%s",
        s.APP_ENV,
        s.VISION_PROVIDER,
        s.RECOMMENDER_PROVIDER,
        CONTRACT_VERSION,
    )
    cleanup_task = (
        asyncio.create_task(retry_deletions(s.STORAGE_DELETE_RETRY_SECONDS))
        if s.STORAGE_DELETE_RETRY_SECONDS > 0
        else None
    )
    try:
        yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task


def create_app() -> FastAPI:
    s = get_settings()
    s.validate_auth()
    setup_logging(s.LOG_LEVEL)
    app = FastAPI(title="Fitjang API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Retry-After"],
    )
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(profile.router)
    app.include_router(profile.frontend_router)
    app.include_router(garments.router)
    app.include_router(media.router)
    app.include_router(recommendations.router)
    app.include_router(weather.router)

    # 시연 시: 프론트 빌드 결과를 같은 주소(/)에서 제공 → 터널 1개, CORS 불필요
    # 반드시 API 라우터 등록 뒤에 마운트해야 /api 경로가 가려지지 않습니다.
    if s.SERVE_FRONTEND:
        if s.FRONTEND_DIST.is_dir():
            app.mount("/", StaticFiles(directory=s.FRONTEND_DIST, html=True), name="frontend")
        else:
            log.warning("SERVE_FRONTEND=true 이지만 %s 가 없습니다", s.FRONTEND_DIST)
    return app


app = create_app()
