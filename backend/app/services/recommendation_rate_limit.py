"""추천 생성 시작 전에 사용자별 최근 60초 사용량을 DB에 확정합니다."""

import math
import time
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import RecommendationRateLimit, User

REQUEST_LIMIT = 10
WINDOW_SECONDS = 60


class RecommendationRateLimiter:
    def __init__(self, db: Session):
        self.db = db

    def _now(self) -> float:
        if self.db.get_bind().dialect.name == "postgresql":
            # 트랜잭션 시작 시각이 아닌 잠금 획득 후의 DB 시각을 사용합니다.
            return float(self.db.scalar(select(func.extract("epoch", func.clock_timestamp()))))
        return time.time()  # SQLite 단일 요청 테스트용

    def consume(self, user_id: UUID) -> None:
        """인증/본문 검증 후 호출. 허용 요청을 먼저 커밋해 후속 실패에도 사용량을 보존합니다."""
        try:
            # 첫 요청에는 카운터 행이 없으므로 항상 존재하는 사용자 행을 잠급니다.
            # 여러 서버/동시 요청도 같은 사용자의 조회-갱신을 직렬화합니다.
            owner = self.db.scalar(
                select(User.id).where(User.id == user_id, User.is_active.is_(True)).with_for_update()
            )
            if owner is None:
                raise AppError(401, ErrorCode.AUTH_INVALID_TOKEN, "유효하지 않은 인증 토큰입니다")
            now = self._now()
            row = self.db.get(RecommendationRateLimit, user_id, populate_existing=True)
            recent = sorted(t for t in (row.request_times if row else []) if t > now - WINDOW_SECONDS)
            if len(recent) >= REQUEST_LIMIT:
                retry_after = max(1, math.ceil(recent[0] + WINDOW_SECONDS - now))
                raise AppError(
                    429,
                    ErrorCode.RECOMMENDATION_RATE_LIMITED,
                    "추천 요청은 사용자별 60초 동안 최대 10회 가능합니다. 잠시 후 다시 시도해 주세요",
                    headers={"Retry-After": str(retry_after)},
                )
            if row is None:
                row = RecommendationRateLimit(user_id=user_id)
                self.db.add(row)
            row.request_times = [*recent, now]
            self.db.commit()  # 비용이 드는 추천 호출 전에 확정하고 사용자 잠금을 해제합니다.
        except Exception:
            self.db.rollback()
            raise
