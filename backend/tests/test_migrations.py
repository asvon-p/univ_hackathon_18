"""TEST_POSTGRES=1로 실제 PostgreSQL의 격리된 스키마에서 실행합니다."""

import json
import os
import re
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from backend.app.db.base import Base
from backend.app.schemas.garment import ANALYSIS_FIELDS


@pytest.fixture(autouse=True)
def require_isolated_migration_schema(db_session):
    """Never downgrade a public/demo schema, even if a test fixture is misconfigured."""
    if os.environ.get("TEST_POSTGRES") == "1":
        schema = db_session.connection().scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_garments_[0-9a-f]{32}", schema), schema


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_starter_flag_preserves_all_legacy_garment_values(db_session, auth_account):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0023")
    connection.execute(
        text("INSERT INTO garments (id,user_id,original_path,name) VALUES (:id,:user,'legacy.jpg','보존')"),
        {"id": uuid4(), "user": auth_account["id"]},
    )
    before = connection.execute(text("SELECT to_jsonb(g) FROM garments g")).scalar_one()
    command.upgrade(config, "head")
    after = connection.execute(text("SELECT to_jsonb(g) FROM garments g")).scalar_one()
    assert after.pop("is_example") is False
    assert after == before


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_auto_opening_upgrade_preserves_manual_polygons(db_session, auth_account):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0021")
    ids = [uuid4(), uuid4()]
    polygon = [[0.4, 0.1], [0.6, 0.1], [0.65, 0.9], [0.35, 0.9]]
    for gid, points in zip(ids, [[], polygon], strict=True):
        connection.execute(
            text(
                "INSERT INTO garments (id,user_id,original_path,opening_polygon) "
                "VALUES (:id,:user,'preserve.jpg',:points)"
            ),
            {"id": gid, "user": auth_account["id"], "points": json.dumps(points)},
        )
    before = connection.execute(text("SELECT to_jsonb(g) FROM garments g ORDER BY id")).scalars().all()
    command.upgrade(config, "head")
    after = (
        connection.execute(text("SELECT to_jsonb(g)-'opening_mode'-'is_example' FROM garments g ORDER BY id"))
        .scalars()
        .all()
    )
    assert before == after
    modes = dict(connection.execute(text("SELECT id,opening_mode FROM garments")).all())
    assert modes[ids[0]] == "auto" and modes[ids[1]] == "manual"


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_recommendation_request_upgrade_preserves_existing_results(db_session, auth_account):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0019")
    connection.execute(
        text(
            "INSERT INTO recommendations (id,user_id,context,provider,latency_ms) "
            "VALUES (:id,:user,'{}','local',12)"
        ),
        {"id": uuid4(), "user": auth_account["id"]},
    )
    before = connection.execute(text("SELECT to_jsonb(r) FROM recommendations r")).scalar_one()
    command.upgrade(config, "head")
    after = connection.execute(text("SELECT to_jsonb(r) FROM recommendations r")).scalar_one()
    assert after == before
    assert connection.scalar(text("SELECT count(*) FROM recommendation_requests")) == 0


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_reference_keyword_upgrade_preserves_legacy_identity_and_features(db_session, auth_account, storage):
    from backend.app.db.models import StyleReference
    from backend.app.services.storage_cleanup import ReferenceCleanupService

    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0018")
    rid = uuid4()
    connection.execute(
        text(
            "INSERT INTO style_references "
            "(id,user_id,version,status,image_path,features,regions,notes,focus) "
            "VALUES (:id,:user,7,'ready',:path,:features,'[]','[]','color')"
        ),
        {
            "id": rid,
            "user": auth_account["id"],
            "path": f"{auth_account['id']}/style-references/{rid}/original.jpg",
            "features": json.dumps([{"category": "top", "color": "red", "extraction": "manual"}]),
        },
    )
    before = connection.execute(
        text("SELECT to_jsonb(r) FROM style_references r WHERE id=:id"), {"id": rid}
    ).scalar_one()
    command.upgrade(config, "head")
    after = connection.execute(
        text("SELECT to_jsonb(r) FROM style_references r WHERE id=:id"), {"id": rid}
    ).scalar_one()
    assert {key: after[key] for key in before} == before
    assert after["image_expires_at"] is not None
    cleanup = ReferenceCleanupService(db_session, storage)
    assert cleanup.expire() == 1
    assert cleanup.drain()["completed"] == 1
    row = db_session.get(StyleReference, rid)
    assert row.version == 7 and row.features == before["features"] and row.status == "ready"
    assert row.image_path is None


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_photo_policy_upgrade_does_not_reactivate_session_token(db_session, api_client, auth_account):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0015")
    connection.execute(
        text("UPDATE users SET photo_policy = 'session' WHERE id = :id"), {"id": auth_account["id"]}
    )
    command.upgrade(config, "head")
    db_session.expire_all()
    assert api_client.get("/api/v1/auth/me", headers=auth_account["headers"]).status_code == 401
    login = api_client.post(
        "/api/v1/auth/login", json={"email": auth_account["email"], "password": auth_account["password"]}
    )
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert api_client.get("/api/v1/profile", headers=headers).json()["photo_policy"] == "persistent"


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_layering_upgrade_preserves_existing_result_ids_and_image_references(db_session, auth_account):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0014")
    connection.execute(
        text(
            "INSERT INTO tryon_results (id,user_id,photo_id,photo_version,items,names,image_id) "
            "VALUES (:id,:user,:photo,'original','{}','[]',:image)"
        ),
        {"id": uuid4(), "user": auth_account["id"], "photo": uuid4(), "image": uuid4()},
    )
    before = connection.execute(text("SELECT to_jsonb(r) FROM tryon_results r")).scalar_one()
    command.upgrade(config, "head")
    after = connection.execute(text("SELECT to_jsonb(r) FROM tryon_results r")).scalar_one()
    assert {k: after[k] for k in before} == before
    assert after["render_version"] == "legacy" and after["layering_status"] == "not_requested"


@pytest.mark.skipif(
    os.environ.get("TEST_POSTGRES") != "1", reason="실제 PostgreSQL 검증은 TEST_POSTGRES=1 필요"
)
def test_migration_matches_models_and_can_be_reversed(db_session):
    connection = db_session.connection()
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    assert compare_metadata(context, Base.metadata) == []
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0001")
    assert set(inspect(connection).get_table_names()) == {"alembic_version"}
    command.upgrade(config, "head")
    assert compare_metadata(context, Base.metadata) == []


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_body_photo_migration_preserves_profiles_and_refuses_lossy_downgrade(db_session, auth_account):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0010")
    before = connection.execute(text("SELECT to_jsonb(p) FROM user_profiles p")).scalar_one()
    command.upgrade(config, "head")
    after = connection.execute(text("SELECT to_jsonb(p) FROM user_profiles p")).scalar_one()
    assert {k: after[k] for k in before} == before
    assert after["body_photo_id"] is None and after["body_photo_updated_at"] is None
    connection.execute(
        text(
            "UPDATE user_profiles SET consent_body_info_at=now(), "
            "body_photo_id=:id, body_photo_updated_at=now()"
        ),
        {"id": uuid4()},
    )
    with pytest.raises(RuntimeError, match="export/cleanup"):
        command.downgrade(config, "0010")


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_profile_migration_preserves_legacy_user_and_garments(db_session):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0002")
    user_id, garment_id = uuid4(), uuid4()
    connection.execute(
        text(
            "INSERT INTO users (id, email, password_hash, nickname) "
            "VALUES (:id, 'legacy@dev.invalid', '!disabled-stage1', 'legacy')"
        ),
        {"id": user_id},
    )
    connection.execute(
        text("INSERT INTO garments (id, user_id, original_path) VALUES (:id, :user, :path)"),
        {"id": garment_id, "user": user_id, "path": f"{user_id}/{garment_id}/original.jpg"},
    )
    command.upgrade(config, "head")
    profile = (
        connection.execute(text("SELECT * FROM user_profiles WHERE user_id = :id"), {"id": user_id})
        .mappings()
        .one()
    )
    assert profile["preferred_styles"] == [] and profile["consent_body_info_at"] is None
    assert (
        connection.execute(text("SELECT is_active FROM users WHERE id = :id"), {"id": user_id}).scalar_one()
        is False
    )
    assert (
        connection.execute(text("SELECT id FROM garments WHERE user_id = :id"), {"id": user_id}).scalar_one()
        == garment_id
    )
    command.downgrade(config, "0002")
    assert (
        connection.execute(text("SELECT id FROM garments WHERE user_id = :id"), {"id": user_id}).scalar_one()
        == garment_id
    )
    command.upgrade(config, "head")


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_reanalysis_migration_protects_legacy_manual_values(db_session):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0007")
    user_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO users (id, email, password_hash, nickname) "
            "VALUES (:id, 'legacy-retry@test.invalid', 'disabled', 'legacy')"
        ),
        {"id": user_id},
    )
    cases = [(uuid4(), "user", "bottom"), (uuid4(), "user", None), (uuid4(), "model", "top")]
    for garment_id, source, category in cases:
        connection.execute(
            text("INSERT INTO garments (id, user_id, original_path) VALUES (:id, :user, :path)"),
            {"id": garment_id, "user": user_id, "path": f"{user_id}/{garment_id}/original.jpg"},
        )
        connection.execute(
            text(
                "INSERT INTO garment_attributes (garment_id, category, color, style_candidates, source) "
                "VALUES (:id, :category, 'navy', ARRAY['minimal']::varchar[], :source)"
            ),
            {"id": garment_id, "category": category, "source": source},
        )
    command.upgrade(config, "head")
    for garment_id, source, category in cases:
        row = (
            connection.execute(
                text("SELECT * FROM garment_attributes WHERE garment_id = :id"), {"id": garment_id}
            )
            .mappings()
            .one()
        )
        expected = (
            ANALYSIS_FIELDS - ({"category"} if category is None else set()) if source == "user" else set()
        )
        assert set(row["user_edited_fields"]) == expected
        assert row["source"] == source and row["category"] == category
        assert row["color"] == "navy" and row["style_candidates"] == ["minimal"]


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_account_migration_marks_legacy_seed_without_changing_accounts(db_session):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0008")
    demo_email = "old-demo@test.invalid"
    demo_id = uuid5(NAMESPACE_URL, f"fitjang:demo:v1:{demo_email}")
    other_id = uuid4()
    for user_id, email in ((demo_id, demo_email), (other_id, "regular@test.invalid")):
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, nickname) "
                "VALUES (:id, :email, 'unchanged', 'legacy')"
            ),
            {"id": user_id, "email": email},
        )
    command.upgrade(config, "head")
    for user_id, expected in ((demo_id, True), (other_id, False)):
        row = connection.execute(text("SELECT * FROM users WHERE id = :id"), {"id": user_id}).mappings().one()
        assert row["is_demo"] is expected and row["password_hash"] == "unchanged"
        assert row["nickname"] == "legacy" and row["is_active"] is True


@pytest.mark.skipif(os.environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration test")
def test_three_slot_migration_preserves_legacy_recommendation_feedback_and_closet(db_session):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0009")
    user_id, recommendation_id, outfit_id, feedback_id = (uuid4() for _ in range(4))
    slots = {slot: uuid4() for slot in ("top", "bottom", "shoes", "outerwear")}
    connection.execute(
        text(
            "INSERT INTO users (id, email, password_hash, nickname) "
            "VALUES (:id, 'contract@test.invalid', 'hash', 'C')"
        ),
        {"id": user_id},
    )
    connection.execute(
        text(
            "INSERT INTO user_profiles (user_id, height_cm, weight_kg, birth_year, "
            "preferred_styles, disliked_colors, consent_body_info_at) "
            "VALUES (:id, 172, 60, 2002, ARRAY['minimal']::varchar[], '{}'::varchar[], now())"
        ),
        {"id": user_id},
    )
    for category, gid in slots.items():
        connection.execute(
            text(
                "INSERT INTO garments (id, user_id, original_path, analysis_status) "
                "VALUES (:id, :user_id, :path, 'done')"
            ),
            {"id": gid, "user_id": user_id, "path": f"{user_id}/{gid}/original.jpg"},
        )
        connection.execute(
            text(
                "INSERT INTO garment_attributes (garment_id, category, style_candidates) "
                "VALUES (:id, :category, ARRAY['minimal']::varchar[])"
            ),
            {"id": gid, "category": category},
        )
    connection.execute(
        text(
            "INSERT INTO recommendations (id, user_id, context, provider, latency_ms) "
            "VALUES (:id, :user_id, CAST(:context AS jsonb), 'local', 10)"
        ),
        {
            "id": recommendation_id,
            "user_id": user_id,
            "context": json.dumps({"user_id": str(user_id), "temperature_c": 8}),
        },
    )
    connection.execute(
        text(
            "INSERT INTO recommendation_outfits "
            "(id, recommendation_id, rank, top_id, bottom_id, shoes_id, outerwear_id, "
            "style_tags, reason, score) VALUES (:id, :recommendation, 1, :top, :bottom, :shoes, "
            ":outerwear, ARRAY['minimal'], 'legacy', 82)"
        ),
        {"id": outfit_id, "recommendation": recommendation_id, **slots},
    )
    connection.execute(
        text(
            "INSERT INTO feedback_events (id, user_id, recommendation_id, outfit_id, accepted, reason) "
            "VALUES (:id, :user_id, :recommendation, :outfit, true, 'keep this feedback')"
        ),
        {"id": feedback_id, "user_id": user_id, "recommendation": recommendation_id, "outfit": outfit_id},
    )
    tables = (
        "users",
        "user_profiles",
        "garments",
        "garment_attributes",
        "recommendations",
        "recommendation_outfits",
        "feedback_events",
    )
    before = {
        table: [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]
        for table in tables
    }
    command.upgrade(config, "head")
    for table in tables:
        after = [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]
        assert len(after) == len(before[table])
        for old, new in zip(before[table], after, strict=True):
            assert {key: new[key] for key in old} == old
    assert connection.scalar(text("SELECT shoes_id FROM recommendation_outfits")) == slots["shoes"]
    # 新 형식도 같은 테이블에 저장됩니다. 기존 기록을 삭제/수정해 제약을 우회하지 않습니다.
    connection.execute(
        text(
            "INSERT INTO recommendation_outfits "
            "(id, recommendation_id, rank, top_id, bottom_id, style_tags, reason) "
            "VALUES (:id, :recommendation, 2, :top, :bottom, ARRAY['minimal'], 'new')"
        ),
        {"id": uuid4(), "recommendation": recommendation_id, **slots},
    )
    with pytest.raises(RuntimeError, match="shoeless outfits"):
        command.downgrade(config, "0009")
