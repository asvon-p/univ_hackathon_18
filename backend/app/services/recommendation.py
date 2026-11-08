import logging
import time
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from backend.app.adapters.base import WeatherPort
from backend.app.adapters.recommender import MockRecommender, SafeRecommender, validate_result
from backend.app.adapters.weather import ManualWeather
from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import Recommendation, RecommendationOutfit
from backend.app.mappers import feedback_contract, garment_contract, garment_view, recommendation_view
from backend.app.repositories.feedback import FeedbackRepository
from backend.app.repositories.garments import GarmentRepository
from backend.app.repositories.profiles import ProfileRepository
from backend.app.repositories.recommendations import RecommendationRepository
from backend.app.schemas.recommendation import RecommendationRequest, RecommendationResponse
from backend.app.services.recommendation_rate_limit import RecommendationRateLimiter
from backend.app.services.user_lock import require_active_user
from backend.app.services.weather import resolve_weather
from shared.contracts import RECOMMENDATION_CATEGORIES, GarmentAttributes, UserContext

log = logging.getLogger(__name__)


class RecommendationService:
    def __init__(self, db: Session):
        self.db = db
        self.garments = GarmentRepository(db)
        self.recommendations = RecommendationRepository(db)

    @staticmethod
    def _require_slots(garments: list[GarmentAttributes]) -> None:
        if not {"top", "bottom"}.issubset({g.category for g in garments}):
            raise AppError(
                422,
                ErrorCode.NOT_ENOUGH_GARMENTS,
                "분석이 완료된 사용 가능한 상의·하의가 각각 필요합니다 (아우터는 선택)",
            )

    def _owned(self, user_id: UUID, recommendation_id: UUID) -> Recommendation:
        record = self.recommendations.get(user_id, recommendation_id)
        if record is None:
            raise AppError(404, ErrorCode.RECOMMENDATION_NOT_FOUND, "추천을 찾을 수 없습니다")
        return record

    def _views(self, user_id: UUID, records: list[Recommendation]) -> list[RecommendationResponse]:
        ids = {
            gid
            for record in records
            for outfit in record.outfits
            for gid in (outfit.top_id, outfit.bottom_id, outfit.shoes_id, outfit.outerwear_id)
            if gid
        }
        garments = {str(g.id): garment_view(g) for g in self.garments.by_ids(user_id, ids)}
        return [recommendation_view(record, garments) for record in records]

    def get(self, user_id: UUID, recommendation_id: UUID) -> RecommendationResponse:
        return self._views(user_id, [self._owned(user_id, recommendation_id)])[0]

    def list(self, user_id: UUID, limit: int, offset: int) -> list[RecommendationResponse]:
        return self._views(user_id, self.recommendations.list(user_id, limit, offset))

    def create(
        self,
        user_id: UUID,
        body: RecommendationRequest,
        recommender: SafeRecommender,
        weather: WeatherPort | None = None,
    ) -> RecommendationResponse:
        RecommendationRateLimiter(self.db).consume(user_id)
        weather_result = resolve_weather(weather or ManualWeather(), body.lat, body.lon, body.temperature_c)
        profile = ProfileRepository(self.db).get(user_id)
        context = UserContext(
            user_id=str(user_id),
            temperature_c=weather_result.temperature_c,
            occasion=body.occasion,
            preferred_styles=body.preferred_styles or (profile.preferred_styles if profile else []),
            excluded_items=[str(gid) for gid in body.excluded_items],
            recent_feedback=[
                feedback_contract(event) for event in FeedbackRepository(self.db).recent(user_id)
            ],
        )
        initial = [
            garment_contract(g)
            for g in self.garments.available(user_id, body.excluded_items)
            if g.attributes.category in RECOMMENDATION_CATEGORIES
        ]
        self._require_slots(initial)
        started = time.perf_counter()
        # 엔진이나 주입된 어댑터가 입력을 변경해도 검증 기준과 저장할 context는 보존합니다.
        outcome = recommender.recommend(
            context.model_copy(deep=True), [g.model_copy(deep=True) for g in initial]
        )

        # 모델을 기다리는 동안 바뀐 상태를 DB에서 다시 읽고 저장 완료까지 잠급니다.
        require_active_user(self.db, user_id)
        current_rows = self.garments.available(user_id, body.excluded_items, lock=True)
        current = [
            garment_contract(g) for g in current_rows if g.attributes.category in RECOMMENDATION_CATEGORIES
        ]
        self._require_slots(current)
        result, _ = validate_result(outcome.result, context, current)
        provider = "fallback" if outcome.is_fallback else outcome.provider
        if not result.outfits:
            log.info("추천 처리 중 옷장 상태 변경 → 현재 옷장으로 대체 추천")
            result, _ = validate_result(MockRecommender().recommend(context, current), context, current)
            provider = "fallback"

        record = Recommendation(
            id=uuid4(),
            user_id=user_id,
            context={**context.model_dump(mode="json"), "weather": weather_result.model_dump(mode="json")},
            provider=provider,
            latency_ms=int((time.perf_counter() - started) * 1000),
            outfits=[
                RecommendationOutfit(
                    id=uuid4(),
                    rank=rank,
                    top_id=UUID(outfit.items.top),
                    bottom_id=UUID(outfit.items.bottom),
                    shoes_id=None,
                    outerwear_id=UUID(outfit.items.outerwear) if outfit.items.outerwear else None,
                    style_tags=outfit.style_tags,
                    reason=outfit.reason,
                    score=outfit.score,
                )
                for rank, outfit in enumerate(result.outfits, start=1)
            ],
        )
        # 추천과 모든 코디를 한 트랜잭션으로 저장합니다. 일부 코디만 저장되지 않습니다.
        try:
            self.recommendations.add(record)
            response = recommendation_view(record, {str(g.id): garment_view(g) for g in current_rows})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return response
