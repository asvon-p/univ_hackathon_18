from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.app.db.models import ReferenceDeletionJob, StyleReference
from backend.app.services.storage_cleanup import ReferenceCleanupService
from backend.tests.api.test_style_references import confirm_reference, upload_reference

REGIONS = [
    {"category": "top", "x": 0, "y": 0, "width": 1, "height": 0.5},
    {"category": "bottom", "x": 0, "y": 0.5, "width": 1, "height": 0.5},
]


def analyze(client, account, ref):
    return client.post(
        f"/api/v1/style-references/{ref['id']}/analyze",
        headers=account["headers"],
        json={"expected_version": ref["version"], "regions": REGIONS},
    )


def test_one_off_batch_keywords_survive_without_images(api_client, auth_account, db_session, storage):
    ref = upload_reference(api_client, auth_account)
    assert ref["retention_policy"] == "keywords_only" and ref["image_available"]
    key = db_session.get(StyleReference, UUID(ref["id"])).image_path
    response = analyze(api_client, auth_account, ref)
    assert response.status_code == 200, response.text
    data = response.json()
    assert [f["color"] for f in data["features"]] == ["red", "blue"]
    assert len(data["features"]) == 2 and data["regions"] == []
    assert not data["image_available"] and data["image_url"] is None
    assert not storage.exists(key)
    path = f"/api/v1/style-references/{ref['id']}"
    assert api_client.get(path + "/image", headers=auth_account["headers"]).status_code == 404
    assert analyze(api_client, auth_account, data).status_code == 409
    ready = confirm_reference(api_client, auth_account, data)
    assert ready["id"] == ref["id"] and ready["status"] == "ready"
    db_session.expire_all()
    saved = api_client.get(path, headers=auth_account["headers"]).json()
    assert saved["features"] == ready["features"] and saved["image_url"] is None


def test_expired_upload_inaccessible_before_worker_and_keywords_preserved(
    api_client, auth_account, account_factory, db_session, storage
):
    ref = upload_reference(api_client, auth_account)
    row = db_session.get(StyleReference, UUID(ref["id"]))
    key = row.image_path
    row.features = [{"category": "top", "color": "red", "extraction": "manual"}]
    row.status = "ready"
    row.image_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    other = account_factory()
    path = f"/api/v1/style-references/{ref['id']}"
    assert api_client.get(path + "/image", headers=auth_account["headers"]).status_code == 404
    assert api_client.get(path, headers=other["headers"]).status_code == 404
    assert analyze(api_client, other, ref).status_code == 404
    assert analyze(api_client, auth_account, ref).status_code == 409
    cleanup = ReferenceCleanupService(db_session, storage)
    assert cleanup.expire() == 1
    assert cleanup.drain()["completed"] == 1
    assert not storage.exists(key)
    db_session.expire_all()
    row = db_session.get(StyleReference, UUID(ref["id"]))
    assert row.version == 1 and row.status == "ready" and row.features[0]["color"] == "red"
    assert row.image_path is None and row.image_expires_at is None and row.regions == []
    assert cleanup.expire() == 0


def test_file_failure_is_private_and_retried_without_deleting_keywords(
    api_client, auth_account, db_session, storage, monkeypatch
):
    ref = upload_reference(api_client, auth_account)
    key = db_session.get(StyleReference, UUID(ref["id"])).image_path
    original = storage.delete_prefix
    monkeypatch.setattr(storage, "delete_prefix", lambda _: (_ for _ in ()).throw(OSError("fixture")))
    data = analyze(api_client, auth_account, ref).json()
    assert data["features"] and not data["image_available"] and storage.exists(key)
    assert db_session.get(ReferenceDeletionJob, UUID(ref["id"])) is not None
    assert (
        api_client.get(
            f"/api/v1/style-references/{ref['id']}/image", headers=auth_account["headers"]
        ).status_code
        == 404
    )
    monkeypatch.setattr(storage, "delete_prefix", original)
    assert ReferenceCleanupService(db_session, storage).process(UUID(ref["id"]))
    assert not storage.exists(key)
    assert db_session.get(StyleReference, UUID(ref["id"])).features == data["features"]


@pytest.mark.parametrize("action", ["analyze", "confirm", "expire"])
def test_commit_failure_does_not_remove_original(
    api_client, auth_account, db_session, storage, monkeypatch, action
):
    ref = upload_reference(api_client, auth_account)
    row = db_session.get(StyleReference, UUID(ref["id"]))
    key = row.image_path
    if action == "expire":
        row.image_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.commit()
    with monkeypatch.context() as scoped:
        scoped.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("fixture")))
        with pytest.raises(RuntimeError):
            if action == "analyze":
                analyze(api_client, auth_account, ref)
            elif action == "confirm":
                confirm_reference(api_client, auth_account, ref)
            else:
                ReferenceCleanupService(db_session, storage).expire()
    db_session.rollback()
    assert storage.exists(key) and db_session.get(StyleReference, UUID(ref["id"])).image_path == key


def test_extraction_exception_cannot_retain_original(
    api_client, auth_account, db_session, storage, monkeypatch
):
    from backend.app.services import style_references

    ref = upload_reference(api_client, auth_account)
    key = db_session.get(StyleReference, UUID(ref["id"])).image_path
    monkeypatch.setattr(
        style_references, "extract_crop", lambda *args: (_ for _ in ()).throw(RuntimeError("fixture"))
    )
    with pytest.raises(RuntimeError):
        analyze(api_client, auth_account, ref)
    assert not storage.exists(key)
    row = db_session.get(StyleReference, UUID(ref["id"]))
    assert row.image_path is None and row.version == 2
    # Manual keyword entry recovers a failed analysis without restoring the photo.
    ready = confirm_reference(api_client, auth_account, {**ref, "version": 2})
    assert ready["status"] == "ready" and not ready["image_available"]


def test_rejected_batch_does_not_consume_image(api_client, auth_account):
    ref = upload_reference(api_client, auth_account)
    for regions in ([REGIONS[0]] * 2, [], [REGIONS[0]] * 4):
        response = api_client.post(
            f"/api/v1/style-references/{ref['id']}/analyze",
            headers=auth_account["headers"],
            json={"expected_version": 1, "regions": regions},
        )
        assert response.status_code == 422
    assert (
        api_client.get(
            f"/api/v1/style-references/{ref['id']}/image", headers=auth_account["headers"]
        ).status_code
        == 200
    )
