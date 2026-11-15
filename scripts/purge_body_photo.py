"""Inspect or purge a user's photo data from the shared local/mentor store."""

import argparse
import os
import sys

from scripts.frontend_preview import ROOT, deny_dotenv


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Permanently remove the selected user's photo data"
    )
    args = parser.parse_args()

    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from backend.app.core.errors import AppError
    from backend.app.db.models import BodyPhotoDeletionJob, TryOnDeletionJob, TryOnResult, User, UserProfile
    from backend.app.services.body_photo import BodyPhotoService
    from backend.app.storage.local import LocalStorage
    from scripts.shared_demo_data import LOCAL_DATABASE, LOCAL_SCHEMA

    storage_root = ROOT / ".demo/frontend-preview/persistent/storage"
    if not storage_root.is_dir():
        parser.error("Shared demo storage was not found")
    engine = create_engine(LOCAL_DATABASE, connect_args={"options": f"-csearch_path={LOCAL_SCHEMA}"})
    try:
        with Session(engine) as db:
            user = db.scalar(
                select(User).where(User.email == args.email.strip().lower(), User.is_active.is_(True))
            )
            if user is None or user.photo_policy != "persistent":
                parser.error("Expected an active persistent account in the shared demo database")
            profile = db.get(UserProfile, user.id)
            saved = db.scalar(
                select(func.count()).select_from(TryOnResult).where(TryOnResult.user_id == user.id)
            )
            pending = sum(
                db.scalar(select(func.count()).select_from(model).where(model.user_id == user.id))
                for model in (BodyPhotoDeletionJob, TryOnDeletionJob)
            )
            has_photo = bool(profile and profile.body_photo_id)
            print(f"{user.email}: body_photo={has_photo}, results={saved}, cleanup={pending}")
            if args.apply:
                try:
                    BodyPhotoService(db, LocalStorage(storage_root)).purge(user.id)
                except AppError as exc:
                    parser.exit(1, exc.detail + "\n")
                print("Stored body photo and try-on history removed")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
