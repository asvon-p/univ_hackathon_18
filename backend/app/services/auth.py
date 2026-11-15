import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.security import create_access_token, dummy_password_hash, hash_password, verify_password
from backend.app.db.models import AccountStorageDeletionJob, MediaSession, User, UserProfile
from backend.app.repositories.profiles import ProfileRepository
from backend.app.repositories.users import UserRepository
from backend.app.schemas.auth import LoginRequest, SignupRequest, SignupResponse, TokenResponse, UserView


class AuthService:
    def __init__(self, db: Session, settings: Settings, storage=None):
        self.db = db
        self.settings = settings
        self.users = UserRepository(db)
        self.storage = storage

    def cleanup_failed_signup(self, user_id):
        if not user_id or self.storage is None:
            return
        try:
            # A lost commit acknowledgement must not remove a committed user's images.
            if self.users.get(user_id) is not None:
                return
            self.db.add(AccountStorageDeletionJob(user_id=user_id))
            self.db.commit()
            from backend.app.services.storage_cleanup import AccountStorageCleanupService

            AccountStorageCleanupService(self.db, self.storage).process(user_id)
        except Exception as exc:
            self.db.rollback()
            logging.getLogger(__name__).warning(
                "Failed signup cleanup deferred: %s (%s)", user_id, type(exc).__name__
            )

    def token(self, user):
        token = create_access_token(user.id, self.settings, token_version=user.auth_token_version)
        if user.photo_policy == "session":
            from backend.app.services.media_session import IDLE_MINUTES, token_hash

            self.db.add(
                MediaSession(
                    token_hash=token_hash(token),
                    user_id=user.id,
                    expires_at=datetime.now(UTC) + timedelta(minutes=IDLE_MINUTES),
                )
            )
            self.db.commit()
        return token

    def signup(self, body: SignupRequest) -> SignupResponse:
        if self.users.by_email(str(body.email)) is not None:
            raise AppError(409, ErrorCode.EMAIL_ALREADY_EXISTS, "이미 가입된 이메일입니다")
        user = User(
            email=str(body.email),
            nickname=body.nickname,
            password_hash=hash_password(body.password.get_secret_value()),
            photo_policy=self.settings.NEW_USER_PHOTO_POLICY,
        )
        try:
            self.users.add(user)
            ProfileRepository(self.db).add(
                UserProfile(user_id=user.id, preferred_styles=[], disliked_colors=[])
            )
            if self.settings.STARTER_WARDROBE_ENABLED:
                from backend.app.services.starter_wardrobe import create_starter_wardrobe

                if self.storage is None:
                    raise RuntimeError("Signup storage is required for starter garments")
                create_starter_wardrobe(self.db, self.storage, user.id)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            self.cleanup_failed_signup(user.id)
            if self.users.by_email(str(body.email)) is not None:
                raise AppError(409, ErrorCode.EMAIL_ALREADY_EXISTS, "이미 가입된 이메일입니다") from None
            raise
        except Exception:
            self.db.rollback()
            self.cleanup_failed_signup(user.id)
            raise
        return SignupResponse(
            **UserView.model_validate(user).model_dump(),
            access_token=self.token(user),
            expires_in=self.settings.JWT_EXPIRE_MINUTES * 60,
        )

    def login(self, body: LoginRequest) -> TokenResponse:
        user = self.users.by_email(str(body.email))
        password_hash = user.password_hash if user is not None else dummy_password_hash()
        valid = verify_password(body.password.get_secret_value(), password_hash)
        if not valid or user is None or not user.is_active:
            raise AppError(
                401, ErrorCode.AUTH_INVALID_CREDENTIALS, "이메일 또는 비밀번호가 올바르지 않습니다"
            )
        return TokenResponse(
            access_token=self.token(user),
            expires_in=self.settings.JWT_EXPIRE_MINUTES * 60,
        )
