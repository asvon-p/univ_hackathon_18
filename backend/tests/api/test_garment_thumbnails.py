from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image, ImageDraw
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.vision import SafeVision
from backend.app.db.models import Garment
from backend.app.deps import get_vision
from backend.app.services import garment as garment_module
from backend.app.services.storage_cleanup import StorageCleanupService
from backend.app.services.thumbnails import thumbnail_key
from backend.tests.api.test_garments import upload
from backend.tests.api.test_reanalysis import NewVision


def thumbnail_url(item):
    return f"/api/v1/garments/{item['garment_id']}/image?kind=thumbnail"


def picture(size=(1600, 1200), alpha=False):
    im = Image.new("RGBA" if alpha else "RGB", size, (0, 0, 0, 0) if alpha else "white")
    bounds = (size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4)
    ImageDraw.Draw(im).rectangle(bounds, fill="navy")
    out = BytesIO()
    exif = Image.Exif()
    exif[270] = "private camera metadata"
    im.save(out, format="PNG", exif=exif)
    return out.getvalue()


def test_upload_prepares_small_webp_without_changing_original(garment_client, storage, db_session):
    item = upload(garment_client, picture()).json()
    row = db_session.get(Garment, UUID(item["garment_id"]))
    assert storage.exists(thumbnail_key(row, row.original_path))  # Ready before first image GET.
    original = garment_client.get(item["image_url"]).content
    response = garment_client.get(thumbnail_url(item))
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "private, no-store"
    assert len(response.content) < len(original)
    with Image.open(BytesIO(response.content)) as im:
        assert im.size == (384, 288) and im.format == "WEBP" and not im.getexif()
        assert "icc_profile" not in im.info
    assert garment_client.get(item["image_url"]).content == original
    with Image.open(BytesIO(original)) as im:
        assert im.size == (1600, 1200)


def test_existing_cutout_generates_transparent_cache_once(garment_client, storage, db_session, monkeypatch):
    item = upload(garment_client).json()
    row = db_session.get(Garment, UUID(item["garment_id"]))
    row.cutout_path = f"{row.user_id}/{row.id}/cutout-existing.png"
    cutout = picture(alpha=True)
    storage.save(row.cutout_path, cutout)
    db_session.commit()
    before = garment_client.get(f"/api/v1/garments/{row.id}").json()
    response = garment_client.get(thumbnail_url(item))
    with Image.open(BytesIO(response.content)) as im:
        assert im.size == (384, 288) and im.getpixel((0, 0))[3] == 0
    assert garment_client.get(f"/media/{row.cutout_path}").content == cutout
    cached = Path(storage.local_path(thumbnail_key(row, row.cutout_path)))
    modified = cached.stat().st_mtime_ns
    def unexpected(*args):
        pytest.fail("cache was regenerated")

    monkeypatch.setattr(garment_module, "create_thumbnail", unexpected)
    assert garment_client.get(thumbnail_url(item)).content == response.content
    assert cached.stat().st_mtime_ns == modified
    assert garment_client.get(f"/api/v1/garments/{row.id}").json() == before
    # Derived paths are never exposed as arbitrary storage/media URLs.
    assert garment_client.get(f"/media/{thumbnail_key(row, row.cutout_path)}").status_code == 404


def test_thumbnail_checks_owner_before_storage_even_when_cached(
    garment_client, account_factory, auth_account, storage, monkeypatch
):
    item = upload(garment_client).json()
    other = account_factory()
    assert garment_client.get(thumbnail_url(item)).status_code == 200
    with monkeypatch.context() as patch:
        patch.setattr(storage, "exists", lambda *args: pytest.fail("unauthorized storage lookup"))
        assert garment_client.get(thumbnail_url(item), headers=other["headers"]).status_code == 404
        assert garment_client.get(thumbnail_url(item), headers={"Authorization": ""}).status_code == 401
    assert garment_client.get(thumbnail_url(item), headers=auth_account["headers"]).status_code == 200


def test_reanalysis_selects_new_thumbnail_and_rollback_keeps_previous(
    garment_client, db_session, monkeypatch
):
    item = upload(garment_client, picture()).json()
    path = f"/api/v1/garments/{item['garment_id']}"
    original = garment_client.get(item["image_url"]).content
    previous = garment_client.get(thumbnail_url(item)).content
    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(NewVision(), 1)
    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError("commit failed")))
        with pytest.raises(SQLAlchemyError):
            garment_client.post(path + "/reanalyze")
    db_session.expunge_all()
    assert garment_client.get(thumbnail_url(item)).content == previous
    assert garment_client.post(path + "/reanalyze").status_code == 200
    assert garment_client.get(thumbnail_url(item)).content != previous
    assert garment_client.get(item["image_url"]).content == original


def test_thumbnail_disk_failure_preserves_registration_and_serves_full_image(
    garment_client, storage, monkeypatch
):
    save = storage.save

    def failing_cache(key, data):
        if key.endswith(".webp"):
            raise PermissionError("cache disk unavailable")
        return save(key, data)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "save", failing_cache)
        response = upload(garment_client, picture())
        assert response.status_code == 201
        item = response.json()
        original = garment_client.get(item["image_url"]).content
        assert garment_client.get(thumbnail_url(item)).content == original
    assert garment_client.get(thumbnail_url(item)).headers["content-type"] == "image/webp"


@pytest.mark.parametrize("withdrawal", [False, True])
def test_delete_cleans_thumbnails_and_revokes_image_access(garment_client, storage, withdrawal):
    item = upload(garment_client).json()
    assert garment_client.get(thumbnail_url(item)).status_code == 200
    assert list(storage.root.rglob("*.webp"))
    path = "/api/v1/auth/me" if withdrawal else f"/api/v1/garments/{item['garment_id']}"
    assert garment_client.delete(path).status_code == 204
    assert garment_client.get(thumbnail_url(item)).status_code == (401 if withdrawal else 404)
    assert not list(storage.root.rglob("*.webp"))


def test_failed_delete_commit_preserves_thumbnail_and_file_cleanup_retries(
    garment_client, storage, db_session, monkeypatch
):
    item = upload(garment_client).json()
    image = garment_client.get(thumbnail_url(item)).content
    path = f"/api/v1/garments/{item['garment_id']}"
    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError("commit failed")))
        with pytest.raises(SQLAlchemyError):
            garment_client.delete(path)
    assert garment_client.get(thumbnail_url(item)).content == image
    with monkeypatch.context() as patch:
        patch.setattr(storage, "delete_prefix", lambda *args: (_ for _ in ()).throw(PermissionError("busy")))
        assert garment_client.delete(path).status_code == 204
    assert garment_client.get(thumbnail_url(item)).status_code == 404
    assert list(storage.root.rglob("*.webp"))
    StorageCleanupService(db_session, storage).process(UUID(item["garment_id"]))
    assert not list(storage.root.rglob("*.webp"))


def test_small_images_are_not_upscaled_and_missing_source_is_404(garment_client, storage, db_session):
    item = upload(garment_client).json()
    response = garment_client.get(thumbnail_url(item))
    with Image.open(BytesIO(response.content)) as im:
        assert im.size == (64, 32)
    row = db_session.get(Garment, UUID(item["garment_id"]))
    Path(storage.local_path(row.original_path)).unlink()
    assert garment_client.get(thumbnail_url(item)).status_code == 404
