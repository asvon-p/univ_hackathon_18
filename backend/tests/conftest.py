"""테스트 공통 설정: 항상 Mock 구현 + 테스트 DB 설정으로 실행합니다.
다른 팀원의 코드(A·B)가 미완성이어도 백엔드 테스트는 깨지지 않습니다."""

import os
import sys
from uuid import UUID, uuid4

os.environ.update(
    {
        "APP_ENV": "test",
        "FITJANG_SKIP_DOTENV": "1",
        "WEATHER_PROVIDER": "manual",
        "VISION_PROVIDER": "mock",
        "VISION_CLASSIFIER": "demo",
        "VISION_SEGMENTATION": "flat",
        "RECOMMENDER_PROVIDER": "mock",
        "OPENAI_API_KEY": "",
        "LLM_API_KEY": "",
        "VISION_TIMEOUT_SEC": "2",
        "RECOMMEND_TIMEOUT_SEC": "2",
        "JWT_SECRET": "tests-only-secret-do-not-use-in-production-12345678",
        "SIGNUP_INVITE_CODE": "",
        "STORAGE_DELETE_RETRY_SECONDS": "0",
    }
)


def _deny_dotenv_access(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        name = os.fsdecode(args[0]).replace("\\", "/").rsplit("/", 1)[-1]
        if name.lower() == ".env":
            raise AssertionError("Tests must not open .env; use explicit test settings")


sys.addaudithook(_deny_dotenv_access)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.app.adapters import registry  # noqa: E402
from backend.app.core.config import get_settings  # noqa: E402
from backend.app.db.base import Base  # noqa: E402
from backend.app.deps import get_db, get_storage  # noqa: E402
from backend.app.storage.local import LocalStorage  # noqa: E402


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    os.environ["STORAGE_DIR"] = str(tmp_path_factory.mktemp("storage"))
    get_settings.cache_clear()
    registry.reset()
    from backend.app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def db_session():
    """기본: SQLite. TEST_POSTGRES=1: 테스트 DB의 임시 스키마 + 실제 Alembic.

    PostgreSQL에서는 모든 테스트 커밋을 savepoint로 바꾸고 최상위 트랜잭션을
    롤백하므로 기존 테이블/데이터는 건드리지 않습니다.
    """
    if os.environ.get("TEST_POSTGRES") == "1":
        from alembic import command
        from alembic.config import Config

        engine = create_engine(get_settings().TEST_DATABASE_URL, connect_args={"connect_timeout": 3})
        with engine.connect() as connection, connection.begin() as transaction:
            schema = f"test_garments_{uuid4().hex}"
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("backend/alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            with Session(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            ) as db:
                yield db
            transaction.rollback()
        engine.dispose()
        return

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def api_client(db_session, storage):
    from backend.app.main import create_app

    def _db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app = create_app()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as c:
        yield c


@pytest.fixture
def account_factory(api_client):
    def create(email=None):
        email = email or f"user-{uuid4().hex}@example.com"
        password = "test-password-123"
        signup = api_client.post(
            "/api/v1/auth/signup", json={"email": email, "password": password, "nickname": "테스트 사용자"}
        )
        assert signup.status_code == 201, signup.text
        login = api_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return {
            **signup.json(),
            "password": password,
            "headers": {"Authorization": f"Bearer {login.json()['access_token']}"},
        }

    return create


@pytest.fixture
def auth_account(account_factory):
    return account_factory()


@pytest.fixture
def test_user_id(auth_account):
    return UUID(auth_account["id"])


@pytest.fixture
def garment_client(api_client, auth_account):
    api_client.headers.update(auth_account["headers"])
    return api_client
