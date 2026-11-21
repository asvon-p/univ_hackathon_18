from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.models import Recommendation, RecommendationGeneration, RecommendationRateLimit, User
from backend.app.schemas.recommendation import RecommendationRequest
from backend.app.services.recommendation_requests import RecommendationRequests
from backend.tests.api.test_recommendations import engine_result, use_engine

PATH = "/api/v1/recommendations"


def post(client, account, key=None, **body):
    headers = dict(account["headers"])
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(PATH, headers=headers, json={"temperature_c": 5, **body})


def test_replays_survive_session_reset_and_reads_never_generate(api_client, auth_account, closet, db_session):
    calls = []
    use_engine(api_client, lambda *args: calls.append(1) or engine_result(closet))
    key = str(uuid4())
    first = post(api_client, auth_account, key)
    assert first.status_code == 201, first.text
    result = first.json()
    db_session.expunge_all()
    for _ in range(3):
        assert post(api_client, auth_account, key).json() == result
        assert (
            api_client.get(PATH + "/" + result["recommendation_id"], headers=auth_account["headers"]).json()
            == result
        )
        status = api_client.get(PATH + "/requests/" + key, headers=auth_account["headers"])
        assert status.json()["status"] == "completed"
    assert calls == [1]
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 1
    assert db_session.scalar(select(func.count()).select_from(RecommendationGeneration)) == 1
    assert len(db_session.get(RecommendationRateLimit, UUID(auth_account["id"])).request_times) == 1
    conflict = post(api_client, auth_account, key, temperature_c=20)
    assert conflict.status_code == 409 and conflict.json()["code"] == "RECOMMENDATION_REQUEST_CONFLICT"
    assert calls == [1]
    assert (
        post(api_client, auth_account, str(uuid4())).json()["recommendation_id"]
        != result["recommendation_id"]
    )
    assert calls == [1, 1]


def test_key_and_result_are_scoped_to_logged_in_owner(
    api_client, auth_account, account_factory, closet, add_garment
):
    key = str(uuid4())
    result = post(api_client, auth_account, key).json()
    other = account_factory()
    for suffix in ("/requests/" + key, "/" + result["recommendation_id"]):
        assert api_client.get(PATH + suffix, headers=other["headers"]).status_code == 404
        assert api_client.get(PATH + suffix).status_code == 401
    add_garment(other, "top")
    add_garment(other, "bottom")
    add_garment(other, "outerwear")
    other_result = post(api_client, other, key)
    assert other_result.status_code == 201
    assert other_result.json()["recommendation_id"] != result["recommendation_id"]
    assert not set(other_result.json()["garments"]) & set(result["garments"])


def test_pending_failed_interrupted_and_deleted_requests_never_reexecute(
    api_client, auth_account, closet, db_session
):
    calls = []
    use_engine(api_client, lambda *args: calls.append(1) or engine_result(closet))
    key = str(uuid4())
    row, fresh = RecommendationRequests(db_session).reserve(
        UUID(auth_account["id"]), RecommendationRequest(temperature_c=5), key
    )
    assert fresh
    pending = post(api_client, auth_account, key)
    assert pending.status_code == 409 and pending.headers["Retry-After"] == "2"
    assert (
        api_client.get(PATH + "/requests/" + key, headers=auth_account["headers"]).json()["status"]
        == "pending"
    )
    row.created_at = datetime.now(UTC) - timedelta(minutes=3)
    db_session.commit()
    assert (
        api_client.get(PATH + "/requests/" + key, headers=auth_account["headers"]).json()["status"]
        == "interrupted"
    )
    assert post(api_client, auth_account, key).status_code == 409
    assert calls == []
    row.status = "failed"
    row.error_status, row.error_code, row.error_detail = 500, "RECOMMENDATION_REQUEST_FAILED", "실패"
    db_session.commit()
    assert post(api_client, auth_account, key).status_code == 500
    assert calls == []
    row.status, row.error_status = "completed", None
    db_session.commit()
    assert post(api_client, auth_account, key).status_code == 404
    assert calls == []


def test_failed_result_commit_is_not_retried_with_same_key(
    api_client, auth_account, closet, db_session, monkeypatch
):
    calls = []
    use_engine(api_client, lambda *args: calls.append(1) or engine_result(closet))
    original, commits = db_session.commit, []

    def commit():
        commits.append(1)
        if len(commits) == 3:
            raise SQLAlchemyError("result commit failed")
        original()

    key = str(uuid4())
    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", commit)
        with pytest.raises(SQLAlchemyError):
            post(api_client, auth_account, key)
    assert calls == [1]
    assert post(api_client, auth_account, key).status_code == 500
    assert calls == [1]
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0
    assert (
        api_client.get(PATH + "/requests/" + key, headers=auth_account["headers"]).json()["status"]
        == "failed"
    )


def test_legacy_clients_get_shared_short_window_dedup(api_client, auth_account, closet, db_session):
    calls = []
    use_engine(api_client, lambda *args: calls.append(1) or engine_result(closet))
    first = post(api_client, auth_account, preferred_styles=["minimal", "casual"]).json()
    second = post(api_client, auth_account, preferred_styles=["casual", "minimal"]).json()
    assert first == second and calls == [1]
    row = db_session.scalar(select(RecommendationGeneration))
    row.created_at = datetime.now(UTC) - timedelta(seconds=61)
    db_session.commit()
    assert (
        post(api_client, auth_account, preferred_styles=["minimal", "casual"]).json()["recommendation_id"]
        != first["recommendation_id"]
    )
    assert calls == [1, 1]


def test_account_withdrawal_cascades_request_ledger(api_client, auth_account, closet, db_session):
    assert post(api_client, auth_account, str(uuid4())).status_code == 201
    db_session.execute(delete(User).where(User.id == UUID(auth_account["id"])))
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(RecommendationGeneration)) == 0
