from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import User, UserProfile
from backend.app.repositories.profiles import ProfileRepository
from backend.app.schemas.profile import BODY_FIELDS, ProfilePatch, ProfileView
from backend.app.services.user_lock import require_active_user


class ProfileService:
    def __init__(self, db: Session):
        self.db = db
        self.profiles = ProfileRepository(db)

    def _owned(self, user: User, *, for_update: bool = False) -> UserProfile:
        profile = self.profiles.get(user.id, for_update=for_update)
        if profile is None:
            raise AppError(404, ErrorCode.NOT_FOUND, "프로필을 찾을 수 없습니다")
        return profile

    @staticmethod
    def _view(user: User, profile: UserProfile) -> ProfileView:
        consent = profile.consent_body_info_at
        if consent is not None and consent.tzinfo is None:
            consent = consent.replace(tzinfo=UTC)
        return ProfileView(
            user_id=user.id,
            nickname=user.nickname,
            gender=profile.gender,
            height_cm=profile.height_cm,
            weight_kg=profile.weight_kg,
            birth_year=profile.birth_year,
            age=profile.age,
            preferred_styles=profile.preferred_styles,
            disliked_colors=profile.disliked_colors,
            consent_body_info_at=consent,
            avatar_config=profile.avatar_config,
        )

    def get(self, user: User) -> ProfileView:
        return self._view(user, self._owned(user))

    def patch(self, user: User, body: ProfilePatch) -> ProfileView:
        user = require_active_user(self.db, user.id, exclusive=True)
        profile = self._owned(user, for_update=True)
        values = body.model_dump(exclude_unset=True)
        consent = values.pop("consent_body_info", None)
        has_body_values = any(values.get(field) is not None for field in BODY_FIELDS)
        if has_body_values and (
            consent is False or (consent is not True and profile.consent_body_info_at is None)
        ):
            raise AppError(
                422,
                ErrorCode.BODY_INFO_CONSENT_REQUIRED,
                "신체정보·아바타 설정 저장에는 수집 동의가 필요합니다",
            )
        if consent is True and profile.consent_body_info_at is None:
            profile.consent_body_info_at = datetime.now(UTC)
        elif consent is False:
            profile.consent_body_info_at = None
            for field in BODY_FIELDS:
                setattr(profile, field, None)
        if "nickname" in values:
            user.nickname = values.pop("nickname")
        for field, value in values.items():
            setattr(profile, field, value)
        self.db.commit()
        return self._view(user, profile)
