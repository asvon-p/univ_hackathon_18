from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import FeedbackEvent
from backend.app.mappers import feedback_view
from backend.app.repositories.feedback import FeedbackRepository
from backend.app.repositories.recommendations import RecommendationRepository
from backend.app.schemas.feedback import FeedbackRequest, FeedbackResponse


class FeedbackService:
    def __init__(self, db: Session):
        self.db = db
        self.feedback = FeedbackRepository(db)

    @staticmethod
    def _not_found() -> AppError:
        # 타인/없는 추천·코디/본문 불일치 모두 같은 응답으로 존재 여부를 숨깁니다.
        return AppError(404, ErrorCode.RECOMMENDATION_NOT_FOUND, "추천 코디를 찾을 수 없습니다")

    def create(self, user_id: UUID, recommendation_id: UUID, body: FeedbackRequest) -> FeedbackResponse:
        try:
            if body.recommendation_id != recommendation_id or not self.feedback.lock_owned_outfit(
                user_id, recommendation_id, body.outfit_id
            ):
                raise self._not_found()
            record = FeedbackEvent(
                user_id=user_id,
                recommendation_id=recommendation_id,
                outfit_id=body.outfit_id,
                accepted=body.accepted,
                reason=body.reason,
            )
            self.feedback.add(record)
            response = feedback_view(record)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return response

    def list(self, user_id: UUID, recommendation_id: UUID, limit: int, offset: int) -> list[FeedbackResponse]:
        if RecommendationRepository(self.db).get(user_id, recommendation_id) is None:
            raise self._not_found()
        return [
            feedback_view(event) for event in self.feedback.list(user_id, recommendation_id, limit, offset)
        ]
