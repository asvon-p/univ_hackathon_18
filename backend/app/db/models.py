"""사용자·옷장·추천·피드백. 스키마 변경은 Alembic으로 적용합니다."""

import uuid
from datetime import datetime

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPkMixin


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint("gender IN ('male', 'female', 'other')", name="gender_values"),
        CheckConstraint("height_cm BETWEEN 50 AND 250", name="height_range"),
        CheckConstraint("weight_kg BETWEEN 20 AND 400", name="weight_range"),
        CheckConstraint("birth_year BETWEEN 1900 AND 2100", name="birth_year_range"),
        CheckConstraint("age BETWEEN 0 AND 130", name="age_range"),
        CheckConstraint(
            "consent_body_info_at IS NOT NULL OR (gender IS NULL AND height_cm IS NULL "
            "AND weight_kg IS NULL AND birth_year IS NULL AND age IS NULL AND avatar_config IS NULL)",
            name="body_info_consent",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    gender: Mapped[str | None] = mapped_column(String(10))
    height_cm: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    age: Mapped[int | None] = mapped_column(SmallInteger)
    birth_year: Mapped[int | None] = mapped_column(SmallInteger)
    preferred_styles: Mapped[list[str]] = mapped_column(
        JSON().with_variant(ARRAY(String()), "postgresql"), default=list
    )
    disliked_colors: Mapped[list[str]] = mapped_column(
        JSON().with_variant(ARRAY(String()), "postgresql"), default=list
    )
    consent_body_info_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    avatar_config: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
    )


class Garment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "garments"
    __table_args__ = (
        CheckConstraint("status IN ('available', 'laundry', 'inactive')", name="status_values"),
        CheckConstraint("analysis_status IN ('pending', 'done', 'failed')", name="analysis_status_values"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="available", server_default="available")
    analysis_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    original_path: Mapped[str] = mapped_column(String(500))
    name: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(100))
    cutout_path: Mapped[str | None] = mapped_column(String(500))
    attributes: Mapped["GarmentAttribute"] = relationship(
        back_populates="garment", cascade="all, delete-orphan", single_parent=True, lazy="joined"
    )


class GarmentAttribute(Base):
    __tablename__ = "garment_attributes"
    __table_args__ = (
        CheckConstraint("category IN ('top', 'bottom', 'outerwear', 'shoes')", name="category_values"),
        CheckConstraint("thermal_level BETWEEN 1 AND 5", name="thermal_level_range"),
        CheckConstraint("source IN ('model', 'user')", name="source_values"),
    )

    garment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("garments.id", ondelete="CASCADE"), primary_key=True
    )
    # 분석 실패 시 추측한 카테고리를 저장하지 않습니다. 수동 입력 후 done으로 전환합니다.
    category: Mapped[str | None] = mapped_column(String(20), index=True)
    subcategory: Mapped[str | None] = mapped_column(String(50))
    color: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    pattern: Mapped[str] = mapped_column(String(20), default="unknown", server_default="unknown")
    style_candidates: Mapped[list[str]] = mapped_column(
        JSON().with_variant(ARRAY(String()), "postgresql"), default=list
    )
    thermal_level: Mapped[int | None] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(String(10), default="model", server_default="model")
    user_edited_fields: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, server_default="[]"
    )
    model_version: Mapped[str | None] = mapped_column(String(50))
    raw_output: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    error: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    garment: Mapped[Garment] = relationship(back_populates="attributes")


class RecommendationRateLimit(Base):
    """사용자별 최근 허용 요청 시각(UTC epoch 초), 최대 10개만 유지합니다."""

    __tablename__ = "recommendation_rate_limits"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    request_times: Mapped[list[float]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list, server_default="[]"
    )


class Recommendation(UUIDPkMixin, Base):
    __tablename__ = "recommendations"
    __table_args__ = (CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    context: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    provider: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    outfits: Mapped[list["RecommendationOutfit"]] = relationship(
        back_populates="recommendation",
        cascade="all, delete-orphan",
        order_by="RecommendationOutfit.rank",
        lazy="selectin",
    )


class RecommendationOutfit(UUIDPkMixin, Base):
    __tablename__ = "recommendation_outfits"
    __table_args__ = (
        CheckConstraint("rank BETWEEN 1 AND 3", name="rank_range"),
        UniqueConstraint("recommendation_id", "rank", name="uq_recommendation_outfits_recommendation_rank"),
    )

    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(SmallInteger)
    # 옷 삭제를 막지 않으며, 삭제된 옷을 포함한 코디만 이력에서 함께 제거합니다.
    top_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("garments.id", ondelete="CASCADE"), index=True)
    bottom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("garments.id", ondelete="CASCADE"), index=True)
    shoes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("garments.id", ondelete="CASCADE"), index=True
    )
    outerwear_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("garments.id", ondelete="CASCADE"), index=True
    )
    style_tags: Mapped[list[str]] = mapped_column(JSON().with_variant(ARRAY(String()), "postgresql"))
    reason: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[Recommendation] = relationship(back_populates="outfits")


class FeedbackEvent(UUIDPkMixin, Base):
    __tablename__ = "feedback_events"
    __table_args__ = (CheckConstraint("length(reason) <= 1000", name="reason_length"),)

    # 로그는 보존하고 삭제된 대상의 참조만 끊습니다. 사용자 삭제 시 사유도 익명화합니다.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL"), index=True
    )
    outfit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation_outfits.id", ondelete="SET NULL"), index=True
    )
    accepted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class StorageDeletionJob(Base):
    """의류 삭제와 같은 트랜잭션으로 기록하는 파일 정리 작업. 의류 FK는 두지 않습니다."""

    __tablename__ = "storage_deletion_jobs"
    __table_args__ = (CheckConstraint("attempts >= 0", name="attempts_nonnegative"),)

    garment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID]
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(100))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccountStorageDeletionJob(Base):
    """탈퇴와 함께 기록하는 사용자 폴더 정리 작업. 삭제된 계정의 FK는 두지 않습니다."""

    __tablename__ = "account_storage_deletion_jobs"
    __table_args__ = (CheckConstraint("attempts >= 0", name="attempts_nonnegative"),)

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(100))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# SQLite 테스트에서도 PostgreSQL 0005 마이그레이션과 같은 삭제 정책을 사용합니다.
event.listen(
    FeedbackEvent.__table__,
    "after_create",
    DDL("""
        CREATE TRIGGER anonymize_feedback_on_user_delete BEFORE DELETE ON users
        BEGIN
            UPDATE feedback_events
            SET user_id = NULL, recommendation_id = NULL, outfit_id = NULL, reason = NULL
            WHERE user_id = OLD.id;
        END
    """).execute_if(dialect="sqlite"),
)
