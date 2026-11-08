from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.adapters.base import WeatherPort
from backend.app.adapters.recommender import SafeRecommender
from backend.app.deps import get_current_user_id, get_db, get_recommender, get_weather
from backend.app.schemas.feedback import FeedbackRequest, FeedbackResponse
from backend.app.schemas.recommendation import RecommendationRequest, RecommendationResponse
from backend.app.services.feedback import FeedbackService
from backend.app.services.recommendation import RecommendationService

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


def get_recommendation_service(db: Session = Depends(get_db)) -> RecommendationService:
    return RecommendationService(db)


def get_feedback_service(db: Session = Depends(get_db)) -> FeedbackService:
    return FeedbackService(db)


@router.post(
    "",
    response_model=RecommendationResponse,
    status_code=201,
    responses={
        429: {
            "description": "로그인 사용자별 최근 60초 10회 초과 (RECOMMENDATION_RATE_LIMITED)",
            "headers": {
                "Retry-After": {"description": "다음 요청까지 기다릴 초", "schema": {"type": "integer"}},
            },
            "content": {
                "application/json": {
                    "example": {
                        "detail": "잠시 후 다시 시도해 주세요",
                        "code": "RECOMMENDATION_RATE_LIMITED",
                    },
                },
            },
        },
    },
)
def create_recommendation(
    body: RecommendationRequest,
    user_id: UUID = Depends(get_current_user_id),
    service: RecommendationService = Depends(get_recommendation_service),
    recommender: SafeRecommender = Depends(get_recommender),
    weather: WeatherPort = Depends(get_weather),
):
    return service.create(user_id, body, recommender, weather)


@router.get("", response_model=list[RecommendationResponse])
def list_recommendations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user_id),
    service: RecommendationService = Depends(get_recommendation_service),
):
    return service.list(user_id, limit, offset)


@router.get("/{recommendation_id}", response_model=RecommendationResponse)
def get_recommendation(
    recommendation_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    service: RecommendationService = Depends(get_recommendation_service),
):
    return service.get(user_id, recommendation_id)


@router.post("/{recommendation_id}/feedback", response_model=FeedbackResponse, status_code=201)
def save_feedback(
    recommendation_id: UUID,
    event: FeedbackRequest,
    user_id: UUID = Depends(get_current_user_id),
    service: FeedbackService = Depends(get_feedback_service),
):
    return service.create(user_id, recommendation_id, event)


@router.get("/{recommendation_id}/feedback", response_model=list[FeedbackResponse])
def list_feedback(
    recommendation_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user_id),
    service: FeedbackService = Depends(get_feedback_service),
):
    return service.list(user_id, recommendation_id, limit, offset)
