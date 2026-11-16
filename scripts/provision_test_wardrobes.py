"""Explicitly provision missing private examples in the shared local/mentor database.

Run with --apply after migration. No dotenv, credential changes, or existing image writes.
Users who already received examples keep their edits and deletions.
"""

import argparse
import json
import os
import sys

from scripts.frontend_preview import ROOT, deny_dotenv


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from backend.app.db.engine import create_database_engine
    from backend.app.db.models import User
    from backend.app.services.starter_wardrobe import provision_existing_user
    from backend.app.storage.local import LocalStorage
    from scripts.shared_demo_data import LOCAL_DATABASE, LOCAL_SCHEMA

    url = make_url(LOCAL_DATABASE).update_query_dict({"options": f"-csearch_path={LOCAL_SCHEMA}"})
    engine = create_database_engine(url, connect_args={"connect_timeout": 3})
    root = ROOT / ".demo/frontend-preview/persistent/storage"
    if not root.is_dir():
        raise RuntimeError("Existing shared storage is missing")
    storage = LocalStorage(root)
    with Session(engine, expire_on_commit=False) as db:
        ids = list(db.scalars(select(User.id).where(User.starter_wardrobe_provisioned.is_(False))))
        db.rollback()
        result = {
            "accounts_pending": len(ids),
            "accounts_added": 0,
            "garments_added": 0,
            "applied": args.apply,
        }
        if args.apply:
            for uid in ids:
                count = provision_existing_user(db, storage, uid)
                result["accounts_added"] += bool(count)
                result["garments_added"] += count
        print(json.dumps(result))
    engine.dispose()


if __name__ == "__main__":
    main()
