import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.recommender import LocalRecommender, SafeRecommender
from backend.app.db.models import FeedbackEvent, Recommendation, RecommendationOutfit, User
from backend.app.deps import get_recommender
from shared.contracts import FeedbackEvent as FeedbackContract


@pytest.fixture
def recommendation(api_client, auth_account, closet):
    response = api_client.post(
        "/api/v1/recommendations", headers=auth_account["headers"], json={"temperature_c": 5}
    )
    assert response.status_code == 201, response.text
    return response.json()


def feedback_path(recommendation):
    return f"/api/v1/recommendations/{recommendation['recommendation_id']}/feedback"


def feedback_body(recommendation, **values):
    return {
        "recommendation_id": recommendation["recommendation_id"],
        "outfit_id": recommendation["outfits"][0]["outfit_id"],
        "accepted": True,
        **values,
    }


def event_count(db):
    return db.scalar(select(func.count()).select_from(FeedbackEvent))


@pytest.mark.parametrize("accepted", [True, False])
def test_save_acceptance_and_rejection_then_read_after_login(
    api_client, auth_account, recommendation, db_session, accepted
):
    path = feedback_path(recommendation)
    assert api_client.get(path, headers=auth_account["headers"]).json() == []
    response = api_client.post(
        path,
        headers=auth_account["headers"],
        json=feedback_body(recommendation, accepted=accepted, reason="  색상 조합에 대한 의견  "),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    row = db_session.get(FeedbackEvent, UUID(body["feedback_id"]))
    assert row.user_id == UUID(auth_account["id"])
    assert row.recommendation_id == UUID(recommendation["recommendation_id"])
    assert row.outfit_id == UUID(recommendation["outfits"][0]["outfit_id"])
    assert row.accepted is accepted and row.reason == "색상 조합에 대한 의견"
    assert body["accepted"] is accepted and body["created_at"].endswith("Z")
    assert "user_id" not in body
    assert FeedbackContract.model_validate(body).accepted is accepted
    assert event_count(db_session) == 1
    db_session.expunge_all()
    login = api_client.post(
        "/api/v1/auth/login", json={"email": auth_account["email"], "password": auth_account["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert api_client.get(path, headers=headers).json() == [body]


@pytest.mark.parametrize("values", [{}, {"reason": None}, {"reason": " \t\n "}, {"reason": "가" * 1000}])
def test_optional_reason_and_length_boundary(api_client, auth_account, recommendation, values):
    response = api_client.post(
        feedback_path(recommendation),
        headers=auth_account["headers"],
        json=feedback_body(recommendation, **values),
    )
    assert response.status_code == 201
    assert response.json()["reason"] == ((values.get("reason") or "").strip() or None)


def test_repeated_posts_append_events_and_history_is_paginated(api_client, auth_account, recommendation):
    path, headers = feedback_path(recommendation), auth_account["headers"]
    created = []
    for accepted in (True, True, False):
        response = api_client.post(
            path, headers=headers, json=feedback_body(recommendation, accepted=accepted)
        )
        assert response.status_code == 201
        created.append(response.json())
    assert len({event["feedback_id"] for event in created}) == 3
    expected = sorted(created, key=lambda e: (e["created_at"], e["feedback_id"]), reverse=True)
    assert api_client.get(path, headers=headers).json() == expected
    assert api_client.get(path + "?limit=1", headers=headers).json() == expected[:1]
    assert api_client.get(path + "?limit=2&offset=1", headers=headers).json() == expected[1:]
    assert api_client.get(path + "?offset=3", headers=headers).json() == []
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert api_client.get(path + "?" + query, headers=headers).status_code == 422


def test_feedback_ownership_and_all_id_combinations(
    api_client, auth_account, account_factory, recommendation, add_garment, db_session
):
    other = account_factory()
    for category in ("top", "bottom", "shoes"):
        add_garment(other, category)
    foreign = api_client.post(
        "/api/v1/recommendations", headers=other["headers"], json={"temperature_c": 20}
    ).json()
    own_second = api_client.post(
        "/api/v1/recommendations", headers=auth_account["headers"], json={"temperature_c": 20}
    ).json()
    path, headers = feedback_path(recommendation), auth_account["headers"]
    # 접근 거부가 이미 저장된 다른 사용자의 피드백을 바꾸거나 노출하지 않습니다.
    saved = api_client.post(feedback_path(foreign), headers=other["headers"], json=feedback_body(foreign))
    assert saved.status_code == 201
    missing = api_client.get(f"/api/v1/recommendations/{uuid4()}/feedback", headers=headers)
    assert missing.status_code == 404
    assert api_client.get(feedback_path(foreign), headers=headers).json() == missing.json()
    assert api_client.get(path, headers=other["headers"]).json() == missing.json()
    assert api_client.get(path + f"?user_id={other['id']}", headers=headers).json() == []
    for target, account_headers, body in (
        (path, other["headers"], feedback_body(recommendation)),
        (feedback_path(foreign), headers, feedback_body(foreign)),
        (path, headers, feedback_body(recommendation, outfit_id=foreign["outfits"][0]["outfit_id"])),
        (path, headers, feedback_body(recommendation, outfit_id=own_second["outfits"][0]["outfit_id"])),
        (path, headers, feedback_body(recommendation, recommendation_id=own_second["recommendation_id"])),
        (path, headers, feedback_body(recommendation, outfit_id=str(uuid4()))),
        (f"/api/v1/recommendations/{uuid4()}/feedback", headers, feedback_body(recommendation)),
    ):
        denied = api_client.post(target, headers=account_headers, json=body)
        assert denied.status_code == 404 and denied.json() == missing.json()
    assert event_count(db_session) == 1
    assert api_client.get(feedback_path(foreign), headers=other["headers"]).json() == [saved.json()]


@pytest.mark.parametrize(
    "values",
    [
        {"accepted": "false"},
        {"accepted": 1},
        {"accepted": None},
        {"outfit_id": "O1"},
        {"recommendation_id": "invalid"},
        {"reason": "가" * 1001},
        {"reason": "invalid\x00text"},
        {"reason": {"text": "invalid"}},
        {"user_id": str(uuid4())},
        {"created_at": "2026-11-07T00:00:00Z"},
    ],
)
def test_invalid_feedback_cannot_write(api_client, auth_account, recommendation, db_session, values):
    response = api_client.post(
        feedback_path(recommendation),
        headers=auth_account["headers"],
        json=feedback_body(recommendation, **values),
    )
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert event_count(db_session) == 0


def test_missing_fields_and_authentication(api_client, auth_account, recommendation, db_session):
    path, body = feedback_path(recommendation), feedback_body(recommendation)
    for field in ("recommendation_id", "outfit_id", "accepted"):
        invalid = {key: value for key, value in body.items() if key != field}
        assert api_client.post(path, headers=auth_account["headers"], json=invalid).status_code == 422
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        assert api_client.post(path, headers=headers, json=body).status_code == 401
        assert api_client.get(path, headers=headers).status_code == 401
    db_session.get(User, UUID(auth_account["id"])).is_active = False
    db_session.commit()
    assert api_client.post(path, headers=auth_account["headers"], json=body).status_code == 401
    assert api_client.get(path, headers=auth_account["headers"]).status_code == 401
    assert event_count(db_session) == 0


def test_historical_feedback_survives_garment_and_recommendation_deletion(
    api_client, auth_account, recommendation, closet, db_session
):
    path, headers, body = (
        feedback_path(recommendation),
        auth_account["headers"],
        feedback_body(recommendation),
    )
    # 피드백은 과거 추천에 대한 의견이므로 현재 세탁 상태와 무관하게 저장됩니다.
    api_client.patch(f"/api/v1/garments/{closet['top']}", headers=headers, json={"status": "laundry"})
    saved = api_client.post(path, headers=headers, json=body)
    assert saved.status_code == 201
    feedback_id = UUID(saved.json()["feedback_id"])
    assert api_client.delete(f"/api/v1/garments/{closet['top']}", headers=headers).status_code == 204
    history = api_client.get(path, headers=headers).json()
    assert history == [{**saved.json(), "outfit_id": None}]
    assert api_client.post(path, headers=headers, json=body).status_code == 404
    assert event_count(db_session) == 1
    db_session.execute(
        delete(Recommendation).where(Recommendation.id == UUID(recommendation["recommendation_id"]))
    )
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(FeedbackEvent, feedback_id)
    assert row.recommendation_id is None and row.outfit_id is None
    assert row.user_id == UUID(auth_account["id"]) and row.accepted is True
    assert api_client.get(path, headers=headers).status_code == 404


def test_user_deletion_keeps_only_anonymous_feedback_statistics(
    api_client, auth_account, recommendation, account_factory, db_session
):
    path = feedback_path(recommendation)
    saved = api_client.post(
        path,
        headers=auth_account["headers"],
        json=feedback_body(recommendation, reason="개인 정보가 포함된 의견"),
    ).json()
    # SQL로 삭제해도 DB 트리거가 적용됩니다(ORM 이벤트에 의존하지 않음).
    db_session.execute(delete(User).where(User.id == UUID(auth_account["id"])))
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(FeedbackEvent, UUID(saved["feedback_id"]))
    assert row.user_id is None and row.recommendation_id is None and row.outfit_id is None
    assert row.reason is None and row.accepted is True and row.created_at is not None
    assert event_count(db_session) == 1
    assert api_client.get(path, headers=auth_account["headers"]).status_code == 401
    assert api_client.get(path, headers=account_factory()["headers"]).status_code == 404


def test_failed_commit_rolls_back_feedback_and_keeps_recommendation(
    api_client, auth_account, recommendation, db_session, monkeypatch
):
    def fail():
        raise SQLAlchemyError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail)
        with pytest.raises(SQLAlchemyError):
            api_client.post(
                feedback_path(recommendation),
                headers=auth_account["headers"],
                json=feedback_body(recommendation),
            )
    assert event_count(db_session) == 0
    assert db_session.get(Recommendation, UUID(recommendation["recommendation_id"])) is not None
    retry = api_client.post(
        feedback_path(recommendation), headers=auth_account["headers"], json=feedback_body(recommendation)
    )
    assert retry.status_code == 201 and event_count(db_session) == 1


def test_next_local_recommendation_receives_only_own_recent_feedback(
    api_client, auth_account, account_factory, recommendation, closet, add_garment, db_session, monkeypatch
):
    import recommendation as engine_module

    path, headers = feedback_path(recommendation), auth_account["headers"]
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(23):
        response = api_client.post(
            path,
            headers=headers,
            json=feedback_body(recommendation, accepted=index % 2 == 0, reason=f"피드백 {index}"),
        )
        assert response.status_code == 201
        row = db_session.get(FeedbackEvent, UUID(response.json()["feedback_id"]))
        # 동일 최상위 트랜잭션을 사용하는 PG 테스트에서도 순서를 명시적으로 검증합니다.
        row.created_at = base + timedelta(seconds=index)
        db_session.commit()
    other = account_factory()
    for category in ("top", "bottom", "shoes"):
        add_garment(other, category)
    foreign = api_client.post(
        "/api/v1/recommendations", headers=other["headers"], json={"temperature_c": 20}
    ).json()
    assert (
        api_client.post(
            feedback_path(foreign),
            headers=other["headers"],
            json=feedback_body(foreign, reason="타인 피드백"),
        ).status_code
        == 201
    )
    seen = []
    original = engine_module.recommend

    def capture(context, garments):
        seen.append(context.model_dump())
        if context.recent_feedback:
            context.recent_feedback[0].reason = "엔진이 입력을 변경"
        return original(context, garments)

    monkeypatch.setattr(engine_module, "recommend", capture)
    adapter = SafeRecommender(LocalRecommender(), timeout_sec=1)
    api_client.app.dependency_overrides[get_recommender] = lambda: adapter
    response = api_client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 20})
    assert response.status_code == 201 and response.json()["provider"] == "local"
    expected = [
        feedback_body(recommendation, accepted=i % 2 == 0, reason=f"피드백 {i}") for i in range(22, 2, -1)
    ]
    assert seen[0]["recent_feedback"] == expected
    assert response.json()["context"]["recent_feedback"] == expected
    row = db_session.get(Recommendation, UUID(response.json()["recommendation_id"]))
    assert row.context["recent_feedback"] == expected
    assert {event["reason"] for event in api_client.get(path + "?limit=100", headers=headers).json()} == {
        f"피드백 {i}" for i in range(23)
    }
    # 클라이언트는 개인화 이력을 직접 주입할 수 없습니다.
    assert (
        api_client.post(
            "/api/v1/recommendations",
            headers=headers,
            json={"temperature_c": 20, "recent_feedback": expected},
        ).status_code
        == 422
    )
    assert api_client.delete(f"/api/v1/garments/{closet['top']}", headers=headers).status_code == 204
    add_garment(auth_account, "top")
    after_delete = api_client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 20})
    assert after_delete.status_code == 201 and seen[-1]["recent_feedback"] == []


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_feedback_migration_preserves_existing_recommendation(
    api_client, auth_account, recommendation, closet, db_session
):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    # 0004 시절의 신발 필수 행과 계약 1.0 context를 명시적으로 재현합니다.
    legacy = db_session.get(Recommendation, UUID(recommendation["recommendation_id"]))
    legacy.context = {key: value for key, value in legacy.context.items() if key != "recent_feedback"}
    for outfit in legacy.outfits:
        outfit.shoes_id = UUID(closet["shoes"])
    db_session.flush()
    recommendation = api_client.get(
        f"/api/v1/recommendations/{recommendation['recommendation_id']}", headers=auth_account["headers"]
    ).json()
    command.downgrade(config, "0004")
    assert "feedback_events" not in inspect(connection).get_table_names()
    assert db_session.get(Recommendation, UUID(recommendation["recommendation_id"])) is not None
    assert db_session.get(RecommendationOutfit, UUID(recommendation["outfits"][0]["outfit_id"])) is not None
    command.upgrade(config, "head")
    response = api_client.post(
        feedback_path(recommendation), headers=auth_account["headers"], json=feedback_body(recommendation)
    )
    assert response.status_code == 201 and event_count(db_session) == 1
    # 업그레이드 후에도 기존 추천 응답은 그대로입니다.
    assert (
        api_client.get(
            f"/api/v1/recommendations/{recommendation['recommendation_id']}", headers=auth_account["headers"]
        ).json()
        == recommendation
    )
