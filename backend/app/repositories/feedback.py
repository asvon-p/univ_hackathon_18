from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import FeedbackEvent, Recommendation, RecommendationOutfit, User


class FeedbackRepository:
    def __init__(self, db: Session):
        self.db = db

    def lock_owned_outfit(self, user_id: UUID, recommendation_id: UUID, outfit_id: UUID) -> bool:
        # 삭제와 피드백 저장이 겹쳐도 검증된 참조가 커밋까지 유지됩니다.
        # 부모 사용자부터 잠가 사용자 삭제의 FK cascade와 잠금 순서를 맞춥니다.
        user = self.db.scalar(
            select(User.id).where(User.id == user_id, User.is_active.is_(True)).with_for_update(read=True)
        )
        if user is None:
            return False
        query = (
            select(RecommendationOutfit.id)
            .join(Recommendation)
            .where(
                Recommendation.user_id == user_id,
                Recommendation.id == recommendation_id,
                RecommendationOutfit.id == outfit_id,
            )
            .with_for_update(read=True, of=(Recommendation, RecommendationOutfit))
        )
        return self.db.scalar(query) is not None

    def add(self, event: FeedbackEvent) -> None:
        self.db.add(event)
        self.db.flush()

    def list(self, user_id: UUID, recommendation_id: UUID, limit: int, offset: int) -> list[FeedbackEvent]:
        query = (
            select(FeedbackEvent)
            .where(FeedbackEvent.user_id == user_id, FeedbackEvent.recommendation_id == recommendation_id)
            .order_by(FeedbackEvent.created_at.desc(), FeedbackEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.scalars(query.execution_options(populate_existing=True)))

    def recent(self, user_id: UUID) -> list[FeedbackEvent]:
        # 삭제/익명화된 기록을 제외하고 실제 소유 추천·코디의 최근 20건만 A에 전달합니다.
        query = (
            select(FeedbackEvent)
            .join(Recommendation, FeedbackEvent.recommendation_id == Recommendation.id)
            .join(RecommendationOutfit, FeedbackEvent.outfit_id == RecommendationOutfit.id)
            .where(
                FeedbackEvent.user_id == user_id,
                Recommendation.user_id == user_id,
                RecommendationOutfit.recommendation_id == Recommendation.id,
            )
            .order_by(FeedbackEvent.created_at.desc(), FeedbackEvent.id.desc())
            .limit(20)
        )
        return list(self.db.scalars(query))
