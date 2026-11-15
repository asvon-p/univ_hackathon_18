"""Local-only administrator role management for the shared demo database; no dotenv."""

import argparse
import os
import sys

from scripts.frontend_preview import deny_dotenv


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--grant", action="store_true")
    group.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    from sqlalchemy import create_engine, select
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from backend.app.db.models import User
    from scripts.shared_demo_data import LOCAL_DATABASE, LOCAL_SCHEMA

    engine = create_engine(
        make_url(LOCAL_DATABASE).update_query_dict({"options": f"-csearch_path={LOCAL_SCHEMA}"})
    )
    try:
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == args.email.strip().lower()).with_for_update())
            if user is None:
                raise SystemExit("Existing account not found; no account or credential was created")
            user.is_admin = args.grant
            db.commit()
        print("Administrator role granted" if args.grant else "Administrator role revoked")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
