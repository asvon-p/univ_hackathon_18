from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.recommender import SafeRecommender
from backend.app.core.config import get_settings
from backend.app.core.security import create_access_token
from backend.app.db.models import Recommendation, RecommendationRateLimit, User
from backend.app.services.recommendation_rate_limit import RecommendationRateLimiter

PATH = "/api/v1/recommendations"


@pytest.fixture
def rate_clock(monkeypatch):
    current = [1_700_000_000.0]
    monkeypatch.setattr(RecommendationRateLimiter, "_now", lambda _: current[0])
    return current


def post(client, account, **kwargs):
    return client.post(PATH, headers=account["headers"], json={"temperature_c": 5}, **kwargs)


def test_ten_requests_then_429_without_engine_or_result_write(
    api_client, auth_account, closet, db_session, rate_clock, monkeypatch
):
    called = []
    original = SafeRecommender.recommend

    def observe(self, *args, **kwargs):
        called.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SafeRecommender, "recommend", observe)
    for _ in range(10):
        response = post(api_client, auth_account)
        assert response.status_code == 201, response.text
    stored = response.json()
    denied = api_client.post(
        PATH,
        headers={**auth_account["headers"], "Origin": "http://localhost:5173"},
        json={"temperature_c": 5},
    )
    assert denied.status_code == 429
    assert denied.json()["code"] == "RECOMMENDATION_RATE_LIMITED"
    assert isinstance(denied.json()["detail"], str)
    assert denied.headers["Retry-After"] == "60"
    assert "retry-after" in denied.headers["Access-Control-Expose-Headers"].lower()
    assert len(called) == 10
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 10
    assert len(db_session.get(RecommendationRateLimit, UUID(auth_account["id"])).request_times) == 10
    # 제한은 생성 POST만 적용합니다. 조회와 피드백은 계속 사용할 수 있습니다.
    path = f"{PATH}/{stored['recommendation_id']}"
    assert api_client.get(PATH, headers=auth_account["headers"]).status_code == 200
    assert api_client.get(path, headers=auth_account["headers"]).status_code == 200
    feedback = api_client.post(
        path + "/feedback",
        headers=auth_account["headers"],
        json={
            "recommendation_id": stored["recommendation_id"],
            "outfit_id": stored["outfits"][0]["outfit_id"],
            "accepted": True,
        },
    )
    assert feedback.status_code == 201
    assert api_client.get(path + "/feedback", headers=auth_account["headers"]).status_code == 200


def test_quota_uses_authenticated_user_not_ip_token_or_query(
    api_client, auth_account, account_factory, db_session, rate_clock
):
    other = account_factory()
    for _ in range(10):
        assert post(api_client, auth_account).json()["code"] == "NOT_ENOUGH_GARMENTS"
    # 같은 IP의 별도 계정은 별도 한도입니다.
    assert post(api_client, other).json()["code"] == "NOT_ENOUGH_GARMENTS"
    login = api_client.post(
        "/api/v1/auth/login", json={"email": auth_account["email"], "password": auth_account["password"]}
    )
    denied = api_client.post(
        PATH + f"?user_id={other['id']}",
        headers={
            "Authorization": "Bearer " + login.json()["access_token"],
            "X-Forwarded-For": "203.0.113.1",
        },
        json={"temperature_c": 5},
    )
    assert denied.status_code == 429
    # 로그인 호출이 같은 초에 같은 JWT를 발급해도 별도 토큰의 공유를 확실히 검증합니다.
    settings = get_settings()
    second_token = create_access_token(
        UUID(auth_account["id"]),
        settings.model_copy(update={"JWT_EXPIRE_MINUTES": settings.JWT_EXPIRE_MINUTES + 1}),
    )
    assert "Bearer " + second_token != auth_account["headers"]["Authorization"]
    assert (
        api_client.post(
            PATH, headers={"Authorization": "Bearer " + second_token}, json={"temperature_c": 5}
        ).status_code
        == 429
    )
    assert len(db_session.get(RecommendationRateLimit, UUID(auth_account["id"])).request_times) == 10
    assert len(db_session.get(RecommendationRateLimit, UUID(other["id"])).request_times) == 1


def test_rolling_window_expires_only_old_slots_and_denials_do_not_extend_it(
    api_client, auth_account, db_session, rate_clock
):
    started = rate_clock[0]
    for _ in range(5):
        assert post(api_client, auth_account).status_code == 422
    rate_clock[0] = started + 30
    for _ in range(5):
        assert post(api_client, auth_account).status_code == 422
    # 분의 경계가 지나도 이전 60초 사용량은 남습니다.
    rate_clock[0] = started + 59.1
    for _ in range(2):
        denied = post(api_client, auth_account)
        assert denied.status_code == 429 and denied.headers["Retry-After"] == "1"
    rate_clock[0] = started + 60
    for _ in range(5):
        assert post(api_client, auth_account).status_code == 422
    denied = post(api_client, auth_account)
    assert denied.status_code == 429 and denied.headers["Retry-After"] == "30"
    row = db_session.get(RecommendationRateLimit, UUID(auth_account["id"]))
    assert row.request_times == [started + 30] * 5 + [started + 60] * 5
    rate_clock[0] = started + 120
    assert post(api_client, auth_account).status_code == 422
    db_session.expire_all()
    assert row.request_times == [started + 120]


def test_unauthenticated_invalid_body_and_inactive_user_do_not_consume_quota(
    api_client, auth_account, db_session, rate_clock
):
    assert api_client.post(PATH, json={"temperature_c": 5}).status_code == 401
    assert (
        api_client.post(
            PATH, headers={"Authorization": "Bearer invalid"}, json={"temperature_c": 5}
        ).status_code
        == 401
    )
    for body in ({"temperature_c": "hot"}, {"temperature_c": 5, "user_id": auth_account["id"]}):
        assert api_client.post(PATH, headers=auth_account["headers"], json=body).status_code == 422
    db_session.get(User, UUID(auth_account["id"])).is_active = False
    db_session.commit()
    denied = post(api_client, auth_account)
    assert denied.status_code == 401 and denied.headers["WWW-Authenticate"] == "Bearer"
    assert db_session.scalar(select(func.count()).select_from(RecommendationRateLimit)) == 0


def test_counter_commit_failure_does_not_call_engine_or_create_recommendation(
    api_client, auth_account, closet, db_session, rate_clock, monkeypatch
):
    def fail():
        raise SQLAlchemyError("quota commit failed")

    def unexpected(*args, **kwargs):
        pytest.fail("engine must not run without a committed quota reservation")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail)
        patch.setattr(SafeRecommender, "recommend", unexpected)
        with pytest.raises(SQLAlchemyError):
            post(api_client, auth_account)
    assert db_session.scalar(select(func.count()).select_from(RecommendationRateLimit)) == 0
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0
    assert post(api_client, auth_account).status_code == 201
    assert len(db_session.get(RecommendationRateLimit, UUID(auth_account["id"])).request_times) == 1


def test_user_deletion_removes_rate_limit_state(api_client, auth_account, db_session, rate_clock):
    assert post(api_client, auth_account).status_code == 422
    user_id = UUID(auth_account["id"])
    db_session.execute(delete(User).where(User.id == user_id))
    db_session.commit()
    db_session.expunge_all()
    assert db_session.get(RecommendationRateLimit, user_id) is None
