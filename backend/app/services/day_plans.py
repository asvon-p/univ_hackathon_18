from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import DayPlan, Recommendation
from backend.app.services.user_lock import require_active_user
from shared.day_plans import DayPlanInput, DayPlanSnapshot


class DayPlanService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def view(row):
        return DayPlanSnapshot(
            id=str(row.id), version=row.version, date=row.day, timezone=row.timezone, events=row.events
        )

    def owned(self, user_id, plan_id, *, version=None, lock=False):
        query = (
            select(DayPlan)
            .where(DayPlan.id == plan_id, DayPlan.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        row = self.db.scalar(query)
        if row is None:
            raise AppError(404, ErrorCode.DAY_PLAN_NOT_FOUND, "일정을 찾을 수 없습니다")
        if version is not None and version != row.version:
            raise AppError(409, ErrorCode.DAY_PLAN_CHANGED, "일정이 변경되었습니다. 다시 선택해 주세요")
        return row

    def list(self, user_id, limit=50, offset=0):
        return [
            self.view(row)
            for row in self.db.scalars(
                select(DayPlan)
                .where(DayPlan.user_id == user_id)
                .order_by(DayPlan.day.desc(), DayPlan.id)
                .limit(limit)
                .offset(offset)
            )
        ]

    def save(self, user_id: UUID, body: DayPlanInput, plan_id=None, version=None):
        require_active_user(self.db, user_id)
        row = (
            self.owned(user_id, plan_id, version=version, lock=True)
            if plan_id
            else DayPlan(user_id=user_id, version=1)
        )
        row.day, row.timezone = body.date, body.timezone
        row.events = [e.model_dump(mode="json") for e in body.events]
        if plan_id:
            row.version += 1
        self.db.add(row)
        self.db.flush()
        result = self.view(row)
        self.db.commit()
        return result

    def delete(self, user_id, plan_id, version):
        require_active_user(self.db, user_id)
        row = self.owned(user_id, plan_id, version=version, lock=True)
        # Remove snapshots and potentially model-written schedule descriptions too.
        records = self.db.scalars(
            select(Recommendation)
            .where(
                Recommendation.user_id == user_id,
                Recommendation.context["schedule_id"].as_string() == str(plan_id),
            )
            .with_for_update()
        ).all()
        for record in records:
            record.context = {
                **record.context,
                "schedule_id": None,
                "schedule_version": None,
                "day_plan": None,
            }
            for outfit in record.outfits:
                outfit.details = {"reference_matches": (outfit.details or {}).get("reference_matches", [])}
                outfit.reason = "일정이 삭제되어 일정별 추천 설명을 정리했습니다"
                outfit.score = None
        self.db.delete(row)
        self.db.commit()
