from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Recommendation


class RecommendationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id: UUID, recommendation_id: UUID) -> Recommendation | None:
        query = select(Recommendation).where(
            Recommendation.id == recommendation_id, Recommendation.user_id == user_id
        )
        return self.db.scalar(query.execution_options(populate_existing=True))

    def list(self, user_id: UUID, limit: int, offset: int) -> list[Recommendation]:
        query = (
            select(Recommendation)
            .where(Recommendation.user_id == user_id)
            .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.scalars(query.execution_options(populate_existing=True)))

    def add(self, recommendation: Recommendation) -> None:
        self.db.add(recommendation)
        self.db.flush()
