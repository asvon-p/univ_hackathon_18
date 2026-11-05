"""팀원 C — FastAPI 진입점.

실행 (저장소 루트에서): uvicorn backend.app.main:app --reload
API 문서: http://localhost:8000/docs  ← D 는 여기서 요청/응답 형식을 확인합니다.
"""
import json
from pathlib import Path

from fastapi import FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware

import recommendation
import vision
from shared.contracts import (
    FeedbackEvent,
    GarmentAttributes,
    RecommendationResult,
    UserContext,
)

MOCKS = Path(__file__).resolve().parents[2] / "mocks"

app = FastAPI(title="Fitjang API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mock_garments() -> list[GarmentAttributes]:
    data = json.loads((MOCKS / "garments.json").read_text(encoding="utf-8"))
    return [GarmentAttributes(**g) for g in data]


@app.get("/health")
def health():
    return {"status": "ok"}


# ---- 아래는 DB 연동 전 Mock 엔드포인트. feat/* 브랜치에서 실제 구현으로 교체 ----

@app.get("/api/v1/garments", response_model=list[GarmentAttributes])
def list_garments():
    return _mock_garments()


@app.post("/api/v1/garments", response_model=GarmentAttributes)
async def upload_garment(file: UploadFile):
    result = vision.extract(image_path=file.filename or "", garment_id="G-new")
    return result.attributes


@app.post("/api/v1/recommendations", response_model=RecommendationResult)
def create_recommendation(context: UserContext):
    return recommendation.recommend(context, _mock_garments())


@app.post("/api/v1/recommendations/{recommendation_id}/feedback")
def save_feedback(recommendation_id: str, event: FeedbackEvent):
    return {"saved": True, "recommendation_id": recommendation_id}
