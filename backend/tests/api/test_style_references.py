from io import BytesIO
from uuid import UUID, uuid4

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import func, select

from backend.app.adapters.recommender import LocalRecommender, SafeRecommender
from backend.app.db.models import Recommendation, ReferenceDeletionJob, StyleReference
from backend.app.deps import get_recommender
from backend.app.services.storage_cleanup import ReferenceCleanupService
from backend.tests.api.test_day_plans import create_plan
from backend.tests.api.test_recommendations import recommend, use_engine


def fixture_photo():
    image = Image.new("RGB", (320, 480), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 319, 239), fill="red")
    draw.rectangle((0, 240, 319, 479), fill="blue")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def upload_reference(client, account, photo=None):
    response = client.post(
        "/api/v1/style-references",
        headers=account["headers"],
        files={"file": ("reference.png", photo or fixture_photo(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def confirm_reference(client, account, row, color="red"):
    response = client.patch(
        "/api/v1/style-references/" + row["id"],
        headers=account["headers"],
        json={
            "expected_version": row["version"],
            "features": [
                {"category": "top", "color": color, "pattern": "solid", "style_candidates": ["minimal"]}
            ],
            "focus": "color",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_region_analysis_confirmation_private_image_and_version(
    api_client, auth_account, account_factory, closet
):
    ref = upload_reference(api_client, auth_account)
    path = "/api/v1/style-references/" + ref["id"]
    other = account_factory()
    assert api_client.get(path + "/image").status_code == 401
    assert api_client.get(path + "/image", headers=other["headers"]).status_code == 404
    assert api_client.get("/api/v1/style-references", headers=other["headers"]).json() == []
    image = api_client.get(path + "/image", headers=auth_account["headers"])
    assert image.status_code == 200 and image.headers["cache-control"] == "private, no-store"
    with Image.open(BytesIO(image.content)) as photo:
        assert photo.format == "JPEG" and not photo.getexif()
    assert recommend(api_client, auth_account, reference_id=ref["id"], reference_version=1).status_code == 422
    payload = {
        "expected_version": 1,
        "region": {"category": "top", "x": 0, "y": 0, "width": 1, "height": 0.5},
    }
    analysis = api_client.post(path + "/analyze", headers=auth_account["headers"], json=payload)
    assert analysis.status_code == 200, analysis.text
    data = analysis.json()
    assert data["version"] == 2 and data["status"] == "draft"
    assert data["features"][0]["color"] == "red" and data["features"][0]["extraction"] == "baseline"
    assert (
        api_client.post(path + "/analyze", headers=auth_account["headers"], json=payload).status_code == 409
    )
    confirmed = confirm_reference(api_client, auth_account, data)
    assert confirmed["status"] == "ready" and confirmed["version"] == 3
    assert confirmed["features"][0]["extraction"] == "manual"


@pytest.mark.parametrize(
    "kind", ["bounds", "zero", "nan", "too-small", "shoe", "duplicate", "unknown", "forged-source"]
)
def test_bad_inputs(api_client, auth_account, kind):
    ref = upload_reference(api_client, auth_account)
    path = "/api/v1/style-references/" + ref["id"]
    if kind in ("duplicate", "unknown", "forged-source"):
        feature = {"category": "top", "color": "red"}
        if kind == "forged-source":
            feature["extraction"] = "vision"
        if kind == "unknown":
            feature["color"] = "unknown"
        features = [feature] * 2 if kind == "duplicate" else [feature]
        result = api_client.patch(
            path, headers=auth_account["headers"], json={"expected_version": 1, "features": features}
        )
    else:
        region = {"category": "top", "x": 0, "y": 0, "width": 1, "height": 0.5}
        if kind == "bounds":
            region["x"] = 0.1
        if kind == "zero":
            region["height"] = 0
        if kind == "nan":
            region["width"] = "NaN"
        if kind == "too-small":
            region["width"] = 0.01
        if kind == "shoe":
            region["category"] = "shoes"
        result = api_client.post(
            path + "/analyze", headers=auth_account["headers"], json={"expected_version": 1, "region": region}
        )
    assert result.status_code == 422, result.text


@pytest.mark.parametrize("provider", ["local", "exception", "timeout", "invalid"])
def test_combined_constraints_and_persistence(api_client, auth_account, closet, provider):
    plan = create_plan(api_client, auth_account)
    ref = confirm_reference(api_client, auth_account, upload_reference(api_client, auth_account))
    if provider == "local":
        api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(LocalRecommender(), 3)
    else:

        def fail(context, garments):
            if provider == "timeout":
                import time

                time.sleep(0.08)
            if provider == "exception":
                raise RuntimeError("test")
            context.required_garment_ids.clear()
            context.style_reference.features.clear()
            return {"recommendation_id": "invalid", "outfits": []}

        use_engine(api_client, fail, timeout=0.01 if provider == "timeout" else 1)
    response = recommend(
        api_client,
        auth_account,
        temperature_c=35,
        reference_id=ref["id"],
        reference_version=ref["version"],
        schedule_id=plan["id"],
        schedule_version=1,
        outerwear_policy="removable",
        required_garment_ids=[closet["top"], closet["outerwear"]],
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["is_fallback"] == (provider != "local")
    for outfit in data["outfits"]:
        assert outfit["items"]["top"] == closet["top"] and outfit["items"]["outerwear"] == closet["outerwear"]
        assert len(outfit["wearing_plan"]) == 2 and outfit["wearing_plan"][0]["outerwear_action"] == "carry"
        assert outfit["reference_matches"][0]["fixed"]
        assert "reference" in outfit["score_components"]
    assert (
        api_client.get(
            "/api/v1/recommendations/" + data["recommendation_id"], headers=auth_account["headers"]
        ).json()
        == data
    )


def test_reference_changes_actual_ranking(api_client, auth_account, add_garment):
    ids = {color: add_garment(auth_account, "top") for color in ("red", "blue")}
    bottom = add_garment(auth_account, "bottom")
    for color, gid in [*ids.items(), ("black", bottom)]:
        assert (
            api_client.patch(
                "/api/v1/garments/" + gid,
                headers=auth_account["headers"],
                json={"color": color, "thermal_level": 3, "pattern": "solid"},
            ).status_code
            == 200
        )
    api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(LocalRecommender(), 3)
    ref = upload_reference(api_client, auth_account)
    for color in ("red", "blue"):
        ref = confirm_reference(api_client, auth_account, ref, color)
        response = recommend(
            api_client,
            auth_account,
            temperature_c=18,
            reference_id=ref["id"],
            reference_version=ref["version"],
        )
        assert response.status_code == 201, response.text
        assert response.json()["outfits"][0]["items"]["top"] == ids[color]


def test_owned_updates_and_recommendation_access(api_client, auth_account, account_factory, closet):
    other = account_factory()
    ref = upload_reference(api_client, other)
    path = "/api/v1/style-references/" + ref["id"]
    assert api_client.get(path, headers=auth_account["headers"]).status_code == 404
    assert (
        api_client.patch(
            path,
            headers=auth_account["headers"],
            json={"expected_version": 1, "features": [{"category": "top", "color": "red"}]},
        ).status_code
        == 404
    )
    assert (
        api_client.delete(path, headers=auth_account["headers"], params={"expected_version": 1}).status_code
        == 404
    )
    calls = []
    use_engine(api_client, lambda *_: calls.append(True))
    for rid in (ref["id"], str(uuid4())):
        assert recommend(api_client, auth_account, reference_id=rid, reference_version=1).status_code == 404
    assert not calls
    assert recommend(api_client, auth_account, reference_id=ref["id"]).status_code == 422
    assert recommend(api_client, auth_account, style_reference={"id": ref["id"]}).status_code == 422


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_reference_changed_during_engine(api_client, auth_account, closet, db_session, change):
    ref = confirm_reference(api_client, auth_account, upload_reference(api_client, auth_account))

    def mutate(context, garments):
        row = db_session.get(StyleReference, UUID(ref["id"]))
        if change == "edit":
            row.version += 1
        else:
            db_session.delete(row)
        db_session.commit()
        return LocalRecommender().recommend(context, garments)

    use_engine(api_client, mutate)
    response = recommend(
        api_client, auth_account, temperature_c=15, reference_id=ref["id"], reference_version=ref["version"]
    )
    assert response.status_code == 409 and response.json()["code"] == "REFERENCE_CHANGED"
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0


def test_deletion_scrubs_snapshots_keeps_schedule_and_retries_files(
    api_client, auth_account, closet, db_session, storage, monkeypatch
):
    plan = create_plan(api_client, auth_account)
    ref = upload_reference(api_client, auth_account)
    row = db_session.get(StyleReference, UUID(ref["id"]))
    key = row.image_path
    original = storage.delete_prefix
    monkeypatch.setattr(storage, "delete_prefix", lambda _: (_ for _ in ()).throw(OSError("fixture")))
    ref = confirm_reference(api_client, auth_account, ref)
    result = recommend(
        api_client,
        auth_account,
        reference_id=ref["id"],
        reference_version=ref["version"],
        schedule_id=plan["id"],
        schedule_version=1,
    ).json()
    assert (
        api_client.delete(
            "/api/v1/style-references/" + ref["id"],
            headers=auth_account["headers"],
            params={"expected_version": ref["version"]},
        ).status_code
        == 204
    )
    job = db_session.get(ReferenceDeletionJob, UUID(ref["id"]))
    assert job and job.attempts >= 1 and storage.exists(key)
    assert (
        api_client.get(
            "/api/v1/style-references/" + ref["id"] + "/image", headers=auth_account["headers"]
        ).status_code
        == 404
    )
    saved = api_client.get(
        "/api/v1/recommendations/" + result["recommendation_id"], headers=auth_account["headers"]
    ).json()
    assert saved["context"]["style_reference"] is None and saved["context"]["day_plan"]["id"] == plan["id"]
    assert all(not o["reference_matches"] and len(o["wearing_plan"]) == 2 for o in saved["outfits"])
    monkeypatch.setattr(storage, "delete_prefix", original)
    assert ReferenceCleanupService(db_session, storage).process(UUID(ref["id"]))
    assert not storage.exists(key)


def test_delete_commit_failure_preserves_file(api_client, auth_account, db_session, storage, monkeypatch):
    ref = upload_reference(api_client, auth_account)
    key = db_session.get(StyleReference, UUID(ref["id"])).image_path

    def fail():
        raise RuntimeError("fixture commit failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(db_session, "commit", fail)
        with pytest.raises(RuntimeError):
            api_client.delete(
                "/api/v1/style-references/" + ref["id"],
                headers=auth_account["headers"],
                params={"expected_version": 1},
            )
    db_session.rollback()
    assert storage.exists(key) and db_session.get(StyleReference, UUID(ref["id"])) is not None


def test_analysis_limit_separate_from_recommendations(api_client, auth_account, account_factory):
    for _ in range(10):
        ref = upload_reference(api_client, auth_account)
        response = api_client.post(
            "/api/v1/style-references/" + ref["id"] + "/analyze",
            headers=auth_account["headers"],
            json={
                "expected_version": ref["version"],
                "region": {"category": "top", "x": 0, "y": 0, "width": 1, "height": 0.5},
            },
        )
        assert response.status_code == 200, response.text
    ref = upload_reference(api_client, auth_account)
    blocked = api_client.post(
        "/api/v1/style-references/" + ref["id"] + "/analyze",
        headers=auth_account["headers"],
        json={
            "expected_version": ref["version"],
            "region": {"category": "top", "x": 0, "y": 0, "width": 1, "height": 0.5},
        },
    )
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
    other = account_factory()
    second = upload_reference(api_client, other)
    assert (
        api_client.post(
            "/api/v1/style-references/" + second["id"] + "/analyze",
            headers=other["headers"],
            json={
                "expected_version": 1,
                "region": {"category": "top", "x": 0, "y": 0, "width": 1, "height": 0.5},
            },
        ).status_code
        == 200
    )


def test_withdrawal_cleans_reference_and_files(api_client, auth_account, db_session, storage):
    ref = upload_reference(api_client, auth_account)
    key = db_session.get(StyleReference, UUID(ref["id"])).image_path
    assert api_client.delete("/api/v1/auth/me", headers=auth_account["headers"]).status_code == 204
    assert not storage.exists(key) and db_session.get(StyleReference, UUID(ref["id"])) is None


def test_upload_commit_failure_removes_written_file(
    api_client, auth_account, db_session, storage, monkeypatch
):
    original_commit = db_session.commit
    original_save = storage.save
    written = []
    commits = 0

    def save(key, data):
        written.append(key)
        return original_save(key, data)

    def commit():
        nonlocal commits
        commits += 1
        if commits == 2:
            raise RuntimeError("fixture persistence failed after file write")
        return original_commit()

    monkeypatch.setattr(storage, "save", save)
    monkeypatch.setattr(db_session, "commit", commit)
    with pytest.raises(RuntimeError):
        upload_reference(api_client, auth_account)
    assert written and all(not storage.exists(key) for key in written)
    assert db_session.scalar(select(func.count()).select_from(StyleReference)) == 0


def test_deleting_day_plan_preserves_reference_comparison(api_client, auth_account, closet):
    plan = create_plan(api_client, auth_account)
    ref = confirm_reference(api_client, auth_account, upload_reference(api_client, auth_account))
    response = recommend(
        api_client,
        auth_account,
        reference_id=ref["id"],
        reference_version=ref["version"],
        schedule_id=plan["id"],
        schedule_version=1,
    )
    assert response.status_code == 201
    assert (
        api_client.delete(
            "/api/v1/day-plans/" + plan["id"], headers=auth_account["headers"], params={"expected_version": 1}
        ).status_code
        == 204
    )
    saved = api_client.get(
        "/api/v1/recommendations/" + response.json()["recommendation_id"], headers=auth_account["headers"]
    ).json()
    assert saved["context"]["style_reference"]["id"] == ref["id"]
    assert saved["context"]["day_plan"] is None
    assert all(o["reference_matches"] and not o["wearing_plan"] for o in saved["outfits"])
