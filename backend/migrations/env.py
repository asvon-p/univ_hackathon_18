from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from backend.app.core.config import get_settings
from backend.app.db import models  # noqa: F401  모든 모델을 로딩해야 autogenerate 가 테이블을 찾습니다
from backend.app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
URL = get_settings().effective_database_url


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        # 통합 테스트가 제공한 격리된 스키마/트랜잭션에서 같은 마이그레이션을 검증합니다.
        context.configure(connection=supplied_connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(URL, poolclass=pool.NullPool, connect_args={"connect_timeout": 3})
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
