import importlib
import os

import pytest
from sqlalchemy import text

from backend.app.core.config import get_settings
from backend.app.db.engine import create_database_engine


def test_native_driver_unavailable_preserves_postgres_schema(monkeypatch):
    if os.environ.get("TEST_POSTGRES") != "1":
        pytest.skip("PostgreSQL driver integration")
    original = importlib.import_module

    def unavailable(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("native driver unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", unavailable)
    from sqlalchemy.engine import make_url

    url = make_url(get_settings().TEST_DATABASE_URL).update_query_dict(
        {"options": "-csearch_path=pg_catalog"}
    )
    engine = create_database_engine(url, connect_args={"connect_timeout": 3})
    try:
        assert engine.dialect.driver == "pg8000"
        with engine.connect() as db:
            assert db.scalar(text("SELECT current_schema()")) == "pg_catalog"
            assert db.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()


def test_fallback_does_not_discard_unknown_or_tls_options(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ImportError("native driver unavailable")

    monkeypatch.setattr(importlib, "import_module", unavailable)
    with pytest.raises(ImportError):
        create_database_engine("postgresql+psycopg://localhost/test?sslmode=verify-full")
