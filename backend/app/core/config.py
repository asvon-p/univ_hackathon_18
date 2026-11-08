"""설정값. 저장소 루트의 .env 를 읽습니다 (실행 위치와 무관)."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    def __init__(self, **values):
        # 테스트·에이전트 작업에서 .env 파일 접근 자체를 생략합니다.
        if os.environ.get("FITJANG_SKIP_DOTENV") == "1":
            values["_env_file"] = None
        super().__init__(**values)

    APP_ENV: Literal["dev", "test", "demo"] = "dev"
    LOG_LEVEL: str = "INFO"

    # docker-compose가 IPv4 loopback에만 포트를 공개하므로 localhost 대신 주소를 명시합니다.
    DATABASE_URL: str = "postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang"
    TEST_DATABASE_URL: str = "postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test"

    JWT_SECRET: str = Field(default="", repr=False)
    JWT_EXPIRE_MINUTES: int = Field(default=1440, gt=0)

    STORAGE_DIR: Path = ROOT_DIR / "storage"
    MAX_UPLOAD_MB: int = 10
    STORAGE_DELETE_RETRY_SECONDS: float = Field(default=30, ge=0)  # 0: 자동 재시도 비활성화

    VISION_PROVIDER: Literal["mock", "local"] = "mock"
    VISION_CLASSIFIER: Literal["demo", "clip"] = "clip"
    VISION_SEGMENTATION: Literal["off", "flat", "rembg"] = "rembg"
    VISION_DEVICE: Literal["cpu", "auto", "cuda", "mps"] = "cpu"
    VISION_DEMO_CATEGORY: Literal["top", "bottom", "outerwear", "shoes"] = "top"
    RECOMMENDER_PROVIDER: Literal["mock", "local", "openai"] = "mock"
    WEATHER_PROVIDER: Literal["manual", "open_meteo"] = "open_meteo"
    WEATHER_TIMEOUT_SEC: float = Field(default=3, gt=0, le=10)
    WEATHER_CACHE_TTL_SEC: float = Field(default=300, ge=0, le=900)
    WEATHER_CACHE_MAX_ENTRIES: int = Field(default=256, ge=1, le=4096)
    VISION_TIMEOUT_SEC: float = 20
    RECOMMEND_TIMEOUT_SEC: float = 30

    LLM_API_KEY: str = Field(default="", repr=False)  # 이전 설정 호환
    OPENAI_API_KEY: str = Field(default="", repr=False)
    OPENAI_MODEL: str = "gpt-6-luna"
    OPENAI_REASONING_EFFORT: Literal["none", "low", "medium", "high"] = "low"
    OPENAI_TIMEOUT_SEC: float = Field(default=6, gt=0, le=60)
    OPENAI_MAX_OUTPUT_TOKENS: int = Field(default=1200, ge=256, le=4096)
    WEATHER_API_KEY: str = ""

    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"  # 쉼표로 구분
    SERVE_FRONTEND: bool = False
    FRONTEND_DIST: Path = ROOT_DIR / "frontend" / "dist"
    SIGNUP_INVITE_CODE: str = ""

    def validate_auth(self) -> None:
        if len(self.JWT_SECRET.encode("utf-8")) < 32 or self.JWT_SECRET.startswith("change-me"):
            raise RuntimeError(
                "JWT_SECRET을 32바이트 이상의 임의 비밀키로 설정하세요: python scripts/init_auth_secret.py"
            )

    @field_validator("STORAGE_DIR", "FRONTEND_DIST")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v if v.is_absolute() else (ROOT_DIR / v).resolve()

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def effective_database_url(self) -> str:
        return self.TEST_DATABASE_URL if self.APP_ENV == "test" else self.DATABASE_URL


@lru_cache
def get_settings() -> Settings:
    return Settings()
