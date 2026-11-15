from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from backend.app.core.config import get_settings
from backend.app.db.models import User
from scripts.shared_demo_data import merge_data


@pytest.fixture
def stores(tmp_path):
    import os

    if os.environ.get("TEST_POSTGRES") != "1":
        pytest.skip("Requires real PostgreSQL")
    url = make_url(get_settings().TEST_DATABASE_URL)
    admin = create_engine(url)
    schemas, engines = [], []
    try:
        for _ in range(2):
            schema = "test_merge_" + uuid4().hex
            schemas.append(schema)
            with admin.begin() as conn:
                conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                cfg = Config("backend/alembic.ini")
                cfg.attributes["connection"] = conn
                command.upgrade(cfg, "head")
            engines.append(create_engine(url.update_query_dict({"options": f"-csearch_path={schema}"})))
        source_root, target_root = tmp_path / "source", tmp_path / "target"
        source_root.mkdir()
        target_root.mkdir()
        yield *engines, source_root, target_root
    finally:
        for engine in engines:
            engine.dispose()
        with admin.begin() as conn:
            for schema in schemas:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def add_user(engine, uid, email):
    with engine.begin() as conn:
        conn.execute(
            User.__table__.insert().values(
                id=uid, email=email, password_hash="preserved-hash", nickname="demo"
            )
        )


def test_merge_preserves_original_users_images_and_is_idempotent(stores):
    source, target, src, dst = stores
    mentor, local = uuid4(), uuid4()
    add_user(source, mentor, "mentor@example.com")
    add_user(target, local, "local@example.com")
    (src / "mentor.jpg").write_bytes(b"mentor image")
    (dst / "local.jpg").write_bytes(b"local image")
    result = merge_data(*stores)
    assert result["rows_added"]["users"] == result["files_added"] == 1
    assert merge_data(*stores)["files_added"] == 0
    with target.connect() as conn:
        assert set(conn.scalars(select(User.id))) == {mentor, local}
        assert conn.scalar(select(User.password_hash).where(User.id == mentor)) == "preserved-hash"
    assert (src / "mentor.jpg").read_bytes() == (dst / "mentor.jpg").read_bytes()
    assert (dst / "local.jpg").read_bytes() == b"local image"


@pytest.mark.parametrize("conflict", ["id", "email", "image"])
def test_conflicting_data_aborts_without_overwriting_existing_data(stores, conflict):
    source, target, src, dst = stores
    uid = uuid4()
    add_user(source, uid, "mentor@example.com")
    local = uid if conflict == "id" else uuid4()
    add_user(target, local, "mentor@example.com" if conflict == "email" else "local@example.com")
    (src / "same.jpg").write_bytes(b"source")
    if conflict == "image":
        (dst / "same.jpg").write_bytes(b"keep")
    with pytest.raises(IntegrityError if conflict == "email" else RuntimeError):
        merge_data(*stores)
    with target.connect() as conn:
        assert list(conn.scalars(select(User.id))) == [local]
    if conflict == "image":
        assert (dst / "same.jpg").read_bytes() == b"keep"
    else:
        assert not (dst / "same.jpg").exists()
