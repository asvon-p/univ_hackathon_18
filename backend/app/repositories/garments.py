from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager

from backend.app.db.models import Garment, GarmentAttribute
from shared.contracts import Category, GarmentStatus


class GarmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(
        self, user_id: UUID, garment_id: UUID, *, lock: bool = False, nowait: bool = False
    ) -> Garment | None:
        query = select(Garment).where(Garment.id == garment_id, Garment.user_id == user_id)
        if lock:
            locked = self.db.scalar(
                select(Garment.id)
                .where(Garment.id == garment_id, Garment.user_id == user_id)
                .with_for_update(nowait=nowait)
            )
            if locked is None:
                return None
            # 잠금을 기다리는 동안 속성도 갱신될 수 있으므로 획득 후 새 문장으로 읽습니다.
            query = query.execution_options(populate_existing=True)
        return self.db.scalar(query)

    def list(
        self, user_id: UUID, category: Category | None = None, status: GarmentStatus | None = None
    ) -> list[Garment]:
        query = select(Garment).where(Garment.user_id == user_id)
        if category is not None:
            query = query.join(Garment.attributes).where(GarmentAttribute.category == category)
        if status is not None:
            query = query.where(Garment.status == status)
        return list(self.db.scalars(query.order_by(Garment.created_at.desc(), Garment.id.desc())))

    def add(self, garment: Garment) -> None:
        self.db.add(garment)
        self.db.flush()

    def available(self, user_id: UUID, excluded: list[UUID], *, lock: bool = False) -> list[Garment]:
        query = (
            select(Garment)
            .join(Garment.attributes)
            .options(contains_eager(Garment.attributes))
            .where(
                Garment.user_id == user_id,
                Garment.status == "available",
                Garment.analysis_status == "done",
                GarmentAttribute.category.is_not(None),
            )
            .order_by(Garment.id)
            .execution_options(populate_existing=True)
        )
        if excluded:
            query = query.where(Garment.id.not_in(excluded))
        if lock:
            # 분석 속성 변경·세탁 상태 변경·삭제와 저장 직전 검증이 엇갈리지 않게 합니다.
            query = query.with_for_update(of=(Garment, GarmentAttribute))
        return list(self.db.scalars(query))

    def by_ids(self, user_id: UUID, ids: set[UUID]) -> list[Garment]:
        if not ids:
            return []
        query = select(Garment).where(Garment.user_id == user_id, Garment.id.in_(ids))
        return list(self.db.scalars(query.execution_options(populate_existing=True)))

    def delete(self, user_id: UUID, garment_id: UUID) -> bool:
        garment = self.get(user_id, garment_id)
        if garment is None:
            return False
        self.db.delete(garment)
        self.db.flush()
        return True
