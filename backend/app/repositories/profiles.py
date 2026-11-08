from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import UserProfile


class ProfileRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id: UUID, *, for_update: bool = False) -> UserProfile | None:
        query = select(UserProfile).where(UserProfile.user_id == user_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.db.scalar(query)

    def add(self, profile: UserProfile) -> None:
        self.db.add(profile)
        self.db.flush()
