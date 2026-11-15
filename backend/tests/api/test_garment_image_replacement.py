from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from backend.app.core.errors import AppError
from backend.app.db.models import Garment, TryOnAdmission
from backend.app.services.garment_image_replacement import ImageReplacement, replace_outerwear_images
from backend.tests.api.test_body_photo import picture


def request(db, gid, image=None):
    row = db.get(Garment, UUID(gid))
    return ImageReplacement(
        row.user_id, row.id, row.original_path, row.cutout_path, row.updated_at, image or picture()
    )


def attributes(row):
    return {c.name: getattr(row.attributes, c.name) for c in row.attributes.__table__.columns}


def test_image_batch_preserves_ids_attributes_originals_and_access(
    api_client, auth_account, account_factory, add_garment, db_session, storage
):
    ids = [add_garment(auth_account, "outerwear") for _ in range(2)]
    updates = [request(db_session, gid) for gid in ids]
    before = {gid: attributes(db_session.get(Garment, UUID(gid))) for gid in ids}
    originals = {r.expected_original: storage._path(r.expected_original).read_bytes() for r in updates}
    records = replace_outerwear_images(db_session, storage, updates)
    other = account_factory()["headers"]
    for record in records:
        gid = record["garment_id"]
        row = db_session.get(Garment, UUID(gid))
        assert attributes(row) == before[gid]
        assert row.original_path == record["new_original"] and row.cutout_path is None
        assert storage._path(record["old_original"]).read_bytes() == originals[record["old_original"]]
        for kind in ("original", "cutout", "thumbnail"):
            url = f"/api/v1/garments/{gid}/image?kind={kind}"
            assert api_client.get(url, headers=auth_account["headers"]).status_code == 200
            assert api_client.get(url, headers=other).status_code == 404
        assert storage.exists(record["new_thumbnail"])


@pytest.mark.parametrize("failure", ["write", "commit"])
def test_failed_batch_keeps_all_old_image_references(
    auth_account, add_garment, db_session, storage, monkeypatch, failure
):
    ids = [add_garment(auth_account, "outerwear") for _ in range(2)]
    updates = [request(db_session, gid) for gid in ids]
    before = {r.expected_original: storage._path(r.expected_original).read_bytes() for r in updates}
    if failure == "commit":
        monkeypatch.setattr(
            db_session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed"))
        )
    else:
        original, calls = storage.save, 0

        def save(key, data):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("second image failed")
            return original(key, data)

        monkeypatch.setattr(storage, "save", save)
    with pytest.raises((OSError, RuntimeError)):
        replace_outerwear_images(db_session, storage, updates)
    for r in updates:
        row = db_session.get(Garment, r.garment_id)
        assert row.original_path == r.expected_original and row.cutout_path == r.expected_cutout
        assert storage._path(r.expected_original).read_bytes() == before[r.expected_original]


@pytest.mark.parametrize("problem", ["owner", "version", "busy"])
def test_replacement_rejects_wrong_owner_stale_review_and_active_tryon(
    auth_account, account_factory, add_garment, db_session, storage, problem
):
    gid = add_garment(auth_account, "outerwear")
    r = request(db_session, gid)
    if problem == "owner":
        r = replace(r, user_id=UUID(account_factory()["id"]))
    elif problem == "version":
        r = replace(r, expected_original="outdated.jpg")
    else:
        now = datetime.now(UTC)
        db_session.add(
            TryOnAdmission(
                job_id=uuid4(),
                user_id=r.user_id,
                expires_at=now + timedelta(minutes=2),
                deadline_at=now + timedelta(minutes=15),
            )
        )
        db_session.commit()
    with pytest.raises(AppError) as error:
        replace_outerwear_images(db_session, storage, [r])
    assert error.value.status == (404 if problem == "owner" else 409)
