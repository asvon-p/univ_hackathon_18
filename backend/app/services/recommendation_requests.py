"""Persist the claim before any paid work; never automatically re-execute a claimed request."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import RecommendationGeneration
from backend.app.services.user_lock import require_active_user


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint(body):
    values = body.model_dump(
        mode="json", exclude={"user_id", "recent_feedback", "day_plan", "style_reference"}
    )
    # Preserve fingerprints of requests made before wardrobe partitions existed.
    if values.get("wardrobe") == "all":
        values.pop("wardrobe")
    for name in ("required_garment_ids", "excluded_items", "preferred_styles"):
        values[name] = sorted(set(values[name]))
    return digest(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


class RecommendationRequests:
    def __init__(self, db):
        self.db = db

    def reserve(self, user_id, body, key):
        require_active_user(self.db, user_id, exclusive=True)
        signature = fingerprint(body)
        query = select(RecommendationGeneration).where(RecommendationGeneration.user_id == user_id)
        if key:
            query = query.where(RecommendationGeneration.key_hash == digest(key))
        else:
            # Legacy API clients get short-window protection even without a client-generated key.
            query = (
                query.where(
                    RecommendationGeneration.automatic.is_(True),
                    RecommendationGeneration.fingerprint == signature,
                    or_(
                        RecommendationGeneration.status == "pending",
                        RecommendationGeneration.created_at >= datetime.now(UTC) - timedelta(seconds=60),
                    ),
                )
                .order_by(RecommendationGeneration.created_at.desc())
                .limit(1)
            )
        row = self.db.scalar(query.execution_options(populate_existing=True))
        if row:
            if row.fingerprint != signature:
                raise AppError(
                    409,
                    ErrorCode.RECOMMENDATION_REQUEST_CONFLICT,
                    "같은 요청 번호로 다른 조건을 보낼 수 없습니다. 조건 화면에서 새 추천을 시작해 주세요",
                )
            self.db.commit()
            return row, False
        row = RecommendationGeneration(
            id=uuid4(),
            user_id=user_id,
            key_hash=digest(key or str(uuid4())),
            fingerprint=signature,
            automatic=key is None,
            status="pending",
        )
        self.db.add(row)
        self.db.commit()
        return row, True

    def get(self, user_id, key):
        row = self.db.scalar(
            select(RecommendationGeneration)
            .where(
                RecommendationGeneration.user_id == user_id,
                RecommendationGeneration.key_hash == digest(key),
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AppError(404, ErrorCode.RECOMMENDATION_NOT_FOUND, "추천 요청을 찾을 수 없습니다")
        return row

    @staticmethod
    def state(row):
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        status = row.status
        if status == "completed" and row.recommendation_id is None:
            status = "deleted"
        if status == "pending" and datetime.now(UTC) - created > timedelta(minutes=2):
            status = "interrupted"
        return {
            "status": status,
            "recommendation_id": str(row.recommendation_id) if row.recommendation_id else None,
            "detail": row.error_detail,
        }

    def complete(self, request_id, recommendation_id):
        row = self.db.get(RecommendationGeneration, request_id, with_for_update=True)
        if row is None or row.status != "pending":
            raise AppError(409, ErrorCode.RECOMMENDATION_REQUEST_FAILED, "추천 요청 상태가 변경되었습니다")
        row.status, row.recommendation_id = "completed", recommendation_id
        # Caller commits this and the recommendation in one transaction.

    def fail(self, request_id, exc):
        self.db.rollback()
        row = self.db.get(RecommendationGeneration, request_id, with_for_update=True, populate_existing=True)
        if row is not None and row.status == "pending":
            row.status = "failed"
            row.error_status = exc.status if isinstance(exc, AppError) else 500
            row.error_code = (
                exc.code if isinstance(exc, AppError) else ErrorCode.RECOMMENDATION_REQUEST_FAILED
            )
            row.error_detail = (
                exc.detail[:500]
                if isinstance(exc, AppError)
                else "추천을 저장하지 못했습니다. 조건 화면에서 새 추천을 시작해 주세요"
            )
            self.db.commit()

    @staticmethod
    def replay_error(row):
        if row.status == "failed":
            raise AppError(
                row.error_status or 500,
                row.error_code or ErrorCode.RECOMMENDATION_REQUEST_FAILED,
                row.error_detail or "이 추천 요청은 실패했습니다",
            )
        raise AppError(
            409,
            ErrorCode.RECOMMENDATION_IN_PROGRESS,
            "이미 접수한 추천입니다. 진행 상태를 확인해 주세요",
            headers={"Retry-After": "2"},
        )
