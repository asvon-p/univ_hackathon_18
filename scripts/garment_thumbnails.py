"""Prepare existing demo garment thumbnails without dotenv, reseeding or changing DB rows."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from scripts.frontend_preview import ROOT, deny_dotenv


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default="postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test"
    )
    parser.add_argument("--schema", default="ui_preview_local")
    parser.add_argument("--storage", type=Path, default=ROOT / ".demo/frontend-preview/persistent/storage")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", args.schema):
        parser.error("Invalid schema")
    if not args.storage.is_dir():
        parser.error("Existing storage directory is required; nothing was initialized")

    from sqlalchemy import create_engine, select
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from backend.app.core.errors import AppError
    from backend.app.db.models import Garment
    from backend.app.services.garment import GarmentService
    from backend.app.storage.local import LocalStorage

    url = make_url(args.database_url).update_query_dict({"options": f"-csearch_path={args.schema}"})
    engine = create_engine(url)
    storage = LocalStorage(args.storage)
    counts = {"ready": 0, "fallback": 0, "skipped_unavailable": 0}
    try:
        with Session(engine) as db:
            records = db.execute(select(Garment.user_id, Garment.id)).all()
        for uid, gid in records:
            with Session(engine) as db:
                try:
                    _, media_type = GarmentService(db, storage).thumbnail(uid, gid)
                    db.commit()  # Releases generation locks; no DB rows are edited.
                    counts["ready" if media_type == "image/webp" else "fallback"] += 1
                except AppError as exc:
                    db.rollback()
                    if exc.status not in (401, 404):
                        raise
                    counts["skipped_unavailable"] += 1
        print(json.dumps(counts))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
