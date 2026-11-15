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
from backend.app.services.day_plans import DayPlanService
from backend.app.services.recommendation_rate_limit import RecommendationRateLimiter
from backend.app.services.recommendation_requests import RecommendationRequests
from backend.app.services.style_references import StyleReferenceService
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

    def _required(self, user_id: UUID, ids: list[UUID]) -> dict[str, str]:
        rows = self.garments.by_ids(user_id, set(ids))
        if len(rows) != len(ids):
            raise AppError(404, ErrorCode.GARMENT_NOT_FOUND, "선택한 옷을 찾을 수 없습니다")
        categories = {}
        for garment in rows:
            if (
                garment.status != "available"
                or garment.analysis_status != "done"
                or garment.attributes is None
                or garment.attributes.category not in RECOMMENDATION_CATEGORIES
            ):
                raise AppError(
                    422,
                    ErrorCode.REQUIRED_GARMENT_UNAVAILABLE,
                    "선택한 옷을 사용할 수 없습니다. 세탁 상태와 분석 결과를 확인해 주세요",
                )
            category = garment.attributes.category
            if category in categories.values():
                raise AppError(
                    422,
                    ErrorCode.REQUIRED_GARMENT_CONFLICT,
                    "상의·하의·아우터에서 각각 한 벌만 필수로 선택해 주세요",
                )
            categories[str(garment.id)] = category
        return categories

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
        *,
        request_key: str | None = None,
    ) -> RecommendationResponse:
        requests = RecommendationRequests(self.db)
        row, fresh = requests.reserve(user_id, body, request_key)
        if not fresh:
            if row.status == "completed":
                if row.recommendation_id is None:
                    raise AppError(404, ErrorCode.RECOMMENDATION_NOT_FOUND, "저장된 추천이 삭제되었습니다")
                return self.get(user_id, row.recommendation_id)
            requests.replay_error(row)
        request_id = row.id
        try:
            return self._create(user_id, body, recommender, weather, request_id)
        except Exception as exc:
            try:
                requests.fail(request_id, exc)
            except Exception:
                self.db.rollback()
                log.warning("추천 요청 실패 상태 기록 보류; 기존 접수는 재실행하지 않음")
            raise

    def _create(self, user_id, body, recommender, weather, request_id):
        RecommendationRateLimiter(self.db).consume(user_id)
        required_categories = self._required(user_id, body.required_garment_ids)
        day_plans = DayPlanService(self.db)
        day_plan = (
            day_plans.view(day_plans.owned(user_id, body.schedule_id, version=body.schedule_version))
            if body.schedule_id
            else None
        )
        references = StyleReferenceService(self.db)
        reference = (
            references.snapshot(
                references.owned(user_id, body.reference_id, version=body.reference_version, ready=True)
            )
            if body.reference_id
            else None
        )
        weather_result = resolve_weather(weather or ManualWeather(), body.lat, body.lon, body.temperature_c)
        profile = ProfileRepository(self.db).get(user_id)
        context = UserContext(
            user_id=str(user_id),
            temperature_c=weather_result.temperature_c,
            occasion=body.occasion,
            preferred_styles=body.preferred_styles or (profile.preferred_styles if profile else []),
            excluded_items=[str(gid) for gid in body.excluded_items],
            required_garment_ids=[str(gid) for gid in body.required_garment_ids],
            schedule_id=str(body.schedule_id) if body.schedule_id else None,
            schedule_version=body.schedule_version,
            day_plan=day_plan,
            reference_id=str(body.reference_id) if body.reference_id else None,
            reference_version=body.reference_version,
            style_reference=reference,
            outerwear_policy=body.outerwear_policy,
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
        if body.schedule_id:
            try:
                day_plans.owned(user_id, body.schedule_id, version=body.schedule_version, lock=True)
            except AppError as exc:
                if exc.status in (404, 409):
                    raise AppError(
                        409,
                        ErrorCode.DAY_PLAN_CHANGED,
                        "추천 중 일정이 변경되거나 삭제되었습니다. 다시 선택해 주세요",
                    ) from None
                raise
        if body.reference_id:
            try:
                references.owned(
                    user_id, body.reference_id, version=body.reference_version, ready=True, lock=True
                )
            except AppError as exc:
                if exc.status in (404, 409, 422):
                    raise AppError(
                        409,
                        ErrorCode.REFERENCE_CHANGED,
                        "추천 중 참고 스타일이 변경되거나 삭제되었습니다. 다시 확인해 주세요",
                    ) from None
                raise
        current_rows = self.garments.available(user_id, body.excluded_items, lock=True)
        current = [
            garment_contract(g) for g in current_rows if g.attributes.category in RECOMMENDATION_CATEGORIES
        ]
        current_categories = {g.garment_id: g.category for g in current}
        if any(current_categories.get(gid) != cat for gid, cat in required_categories.items()):
            raise AppError(
                409,
                ErrorCode.REQUIRED_GARMENT_CHANGED,
                "추천 중 선택한 옷의 상태가 바뀌었습니다. 필수 의류를 다시 확인해 주세요",
            )
        self._require_slots(current)
        result, _ = validate_result(outcome.result, context, current)
        provider = "fallback" if outcome.is_fallback else outcome.provider
        if not result.outfits:
            log.info("추천 처리 중 옷장 상태 변경 → 현재 옷장으로 대체 추천")
            result, _ = validate_result(MockRecommender().recommend(context, current), context, current)
            provider = "fallback"

        if not result.outfits:
            raise AppError(
                422,
                ErrorCode.NO_FEASIBLE_OUTFIT,
                "선택한 옷과 확인된 일정 복장 조건을 모두 만족하는 코디가 없습니다. 조건을 확인해 주세요",
            )

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
                    details=outfit.model_dump(
                        mode="json",
                        include={
                            "wearing_plan",
                            "limitations",
                            "score_components",
                            "policy_version",
                            "reference_matches",
                        },
                    ),
                )
                for rank, outfit in enumerate(result.outfits, start=1)
            ],
        )
        # 추천과 모든 코디를 한 트랜잭션으로 저장합니다. 일부 코디만 저장되지 않습니다.
        try:
            self.recommendations.add(record)
            RecommendationRequests(self.db).complete(request_id, record.id)
            response = recommendation_view(record, {str(g.id): garment_view(g) for g in current_rows})
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return response
