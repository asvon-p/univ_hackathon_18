from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.vision import SafeVision
from backend.app.core.config import Settings, get_settings
from backend.app.db.models import Garment, GarmentAttribute, StorageDeletionJob, User
from backend.app.deps import get_vision
from backend.app.services.garment import GarmentService
from shared.contracts import GarmentAttributes, VisionResult


def image_bytes(format="PNG", size=(64, 32), exif=None):
    stream = BytesIO()
    Image.new("RGB", size, "navy").save(stream, format=format, **({"exif": exif} if exif else {}))
    return stream.getvalue()


def upload(client, data=None):
    return client.post(
        "/api/v1/garments", files={"file": ("../../untrusted.png", data or image_bytes(), "image/png")}
    )


class FailingVision:
    name = "failing-test"

    def extract(self, image_path, garment_id):
        # 실제로 저장된 JPEG가 B에게 전달되어야 합니다.
        with Image.open(image_path) as image:
            assert image.format == "JPEG"
        raise RuntimeError("model unavailable")


def test_upload_persists_and_serves_normalized_image(garment_client, db_session, storage, test_user_id):
    response = upload(garment_client)
    assert response.status_code == 201, response.text
    body = response.json()
    garment_id = UUID(body["garment_id"])
    assert body["analysis_status"] == "done"
    assert body["category"] == "top"  # Mock은 저장된 original.jpg를 보고 고정 기본값을 사용합니다.
    assert body["source"] == "model"
    assert body["created_at"].endswith("Z")
    assert body["image_url"] == f"/media/{test_user_id}/{garment_id}/original.jpg"
    row = db_session.get(Garment, garment_id)
    assert row.attributes.model_version == "mock-1"
    assert row.attributes.raw_output["provider"] == "mock"
    assert row.attributes.raw_output["latency_ms"] >= 0
    assert storage.exists(row.original_path)
    db_session.expunge_all()
    assert garment_client.get(f"/api/v1/garments/{garment_id}").json() == body
    assert garment_client.get("/api/v1/garments").json() == [body]
    media = garment_client.get(body["image_url"])
    assert media.status_code == 200
    assert media.headers["content-type"] == "image/jpeg"
    assert media.headers["cache-control"] == "private, no-store"
    with Image.open(BytesIO(media.content)) as image:
        assert image.format == "JPEG" and image.size == (64, 32)


def test_resize_rotation_and_metadata_removal(garment_client):
    exif = Image.Exif()
    exif[274] = 6  # 90도 회전
    exif[270] = "private metadata"
    response = upload(garment_client, image_bytes("JPEG", (3000, 1500), exif))
    assert response.status_code == 201
    with Image.open(BytesIO(garment_client.get(response.json()["image_url"]).content)) as image:
        assert image.size == (1024, 2048)
        assert not image.getexif()


@pytest.mark.parametrize("data", [b"not an image", image_bytes("GIF"), image_bytes()[:30]])
def test_invalid_upload_leaves_no_rows_or_files(garment_client, db_session, storage, data):
    response = upload(garment_client, data)
    assert response.status_code == 415
    assert response.json()["code"] == "UPLOAD_UNSUPPORTED_TYPE"
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 0
    assert list(storage.root.iterdir()) == []


def test_content_is_validated_instead_of_filename_or_mime(garment_client):
    response = garment_client.post(
        "/api/v1/garments", files={"file": ("wrong.txt", image_bytes(), "text/plain")}
    )
    assert response.status_code == 201


def test_oversized_upload(garment_client, db_session, storage):
    settings = Settings(_env_file=None, APP_ENV="test", MAX_UPLOAD_MB=1)
    garment_client.app.dependency_overrides[get_settings] = lambda: settings
    response = upload(garment_client, b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413
    assert response.json()["code"] == "UPLOAD_TOO_LARGE"
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 0
    assert list(storage.root.iterdir()) == []


def test_failed_analysis_is_saved_and_can_be_fixed(garment_client, db_session):
    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(FailingVision(), 1)
    response = upload(garment_client)
    assert response.status_code == 201
    body = response.json()
    assert body["analysis_status"] == "failed" and body["category"] is None
    assert "model unavailable" in body["error"]
    path = f"/api/v1/garments/{body['garment_id']}"
    assert garment_client.get(body["image_url"]).status_code == 200
    assert garment_client.get("/api/v1/garments").json()[0]["analysis_status"] == "failed"
    # 세탁 상태만 수정한 것으로 분석 완료가 되지는 않습니다.
    assert garment_client.patch(path, json={"status": "laundry"}).json()["analysis_status"] == "failed"
    fixed = garment_client.patch(path, json={"category": "bottom", "color": "navy", "thermal_level": 3})
    assert fixed.status_code == 200
    assert fixed.json()["analysis_status"] == "done"
    assert fixed.json()["source"] == "user" and fixed.json()["error"] is None
    assert fixed.json()["status"] == "laundry"
    assert (
        db_session.get(Garment, UUID(body["garment_id"])).attributes.raw_output["result"]["success"] is False
    )


def test_patch_filters_and_delete(garment_client, db_session, storage, test_user_id):
    first, second = upload(garment_client).json(), upload(garment_client).json()
    path = f"/api/v1/garments/{first['garment_id']}"
    response = garment_client.patch(
        path,
        json={"category": "bottom", "status": "laundry", "style_candidates": ["minimal"], "thermal_level": 4},
    )
    assert response.status_code == 200
    assert response.json()["color"] == first["color"]
    assert response.json()["source"] == "user"
    assert [
        g["garment_id"] for g in garment_client.get("/api/v1/garments?category=bottom&status=laundry").json()
    ] == [first["garment_id"]]
    assert [g["garment_id"] for g in garment_client.get("/api/v1/garments?status=available").json()] == [
        second["garment_id"]
    ]
    assert garment_client.patch(path, json={"thermal_level": None}).json()["thermal_level"] is None
    deleted = garment_client.delete(path)
    assert deleted.status_code == 204 and not deleted.content
    assert db_session.get(GarmentAttribute, UUID(first["garment_id"])) is None
    assert not Path(storage.local_path(f"{test_user_id}/{first['garment_id']}")).exists()
    assert garment_client.get(path).status_code == 404
    assert garment_client.get(first["image_url"]).status_code == 404
    assert garment_client.get(second["image_url"]).status_code == 200


@pytest.mark.parametrize(
    "patch",
    [
        {"category": None},
        {"status": None},
        {"color": "ultraviolet"},
        {"pattern": None},
        {"thermal_level": 0},
        {"thermal_level": 6},
        {"style_candidates": None},
        {"user_id": str(uuid4())},
        {"analysis_status": "done"},
        {"cutout_path": "other/file"},
    ],
)
def test_patch_rejects_invalid_or_server_owned_fields(garment_client, patch):
    body = upload(garment_client).json()
    response = garment_client.patch(f"/api/v1/garments/{body['garment_id']}", json=patch)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_other_user_cannot_access_garments_or_images(garment_client, account_factory, auth_account):
    own = upload(garment_client).json()
    other = account_factory()
    garment_client.headers.update(other["headers"])
    path = f"/api/v1/garments/{own['garment_id']}"
    assert garment_client.get("/api/v1/garments").json() == []
    assert garment_client.get(path).status_code == 404
    assert garment_client.patch(path, json={"status": "inactive"}).status_code == 404
    assert garment_client.delete(path).status_code == 404
    assert garment_client.get(own["image_url"]).status_code == 404
    garment_client.headers.update(auth_account["headers"])
    assert garment_client.get(own["image_url"]).status_code == 200


def test_missing_or_malformed_ids_and_filters(garment_client):
    assert garment_client.get(f"/api/v1/garments/{uuid4()}").status_code == 404
    assert garment_client.get("/api/v1/garments/not-a-uuid").status_code == 422
    assert garment_client.get("/api/v1/garments?category=hat").status_code == 422
    assert garment_client.get("/api/v1/garments?status=deleted").status_code == 422


def test_media_does_not_expose_unregistered_files(garment_client, storage, test_user_id):
    body = upload(garment_client).json()
    prefix = f"{test_user_id}/{body['garment_id']}"
    storage.save(f"{prefix}/private.txt", b"private")
    storage.save(f"{prefix}/cutout.png", image_bytes())
    for suffix in ("private.txt", "cutout.png", "%2e%2e%2fprivate.txt"):
        assert garment_client.get(f"/media/{prefix}/{suffix}").status_code == 404
    Path(storage.local_path(f"{prefix}/original.jpg")).unlink()
    assert garment_client.get(body["image_url"]).status_code == 404


def test_cutout_is_served_only_for_its_garment(garment_client, storage, test_user_id):
    class CutoutVision:
        name = "test"

        def extract(self, image_path, garment_id):
            key = f"{test_user_id}/{garment_id}/cutout.png"
            (Path(image_path).parent / "cutout.png").write_bytes(image_bytes())
            return VisionResult(
                success=True,
                attributes=GarmentAttributes(garment_id=garment_id, category="shoes", cutout_path=key),
            )

    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(CutoutVision(), 1)
    body = upload(garment_client).json()
    assert body["category"] == "shoes" and body["cutout_url"]
    assert garment_client.get(body["cutout_url"]).headers["content-type"] == "image/png"
    garment_client.delete(f"/api/v1/garments/{body['garment_id']}")
    assert not storage.exists(body["cutout_path"])


def test_invalid_model_attributes_do_not_lose_upload(garment_client):
    class InvalidVision:
        name = "test"

        def extract(self, image_path, garment_id):
            return VisionResult(
                success=True,
                attributes=GarmentAttributes(garment_id=garment_id, category="top", thermal_level=99),
            )

    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(InvalidVision(), 1)
    response = upload(garment_client)
    assert response.status_code == 201
    assert response.json()["analysis_status"] == "failed"
    assert garment_client.get(response.json()["image_url"]).status_code == 200


def test_initial_db_failure_cleans_up_upload(db_session, storage, monkeypatch, test_user_id):
    service = GarmentService(db_session, storage)

    def fail():
        raise SQLAlchemyError("db unavailable")

    monkeypatch.setattr(db_session, "commit", fail)
    with pytest.raises(SQLAlchemyError):
        service.upload(test_user_id, BytesIO(image_bytes()), SafeVision(FailingVision(), 1), 1024 * 1024)
    assert not list(storage.root.rglob("*.jpg"))
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 0


def test_file_delete_failure_queues_cleanup_and_blocks_access(
    garment_client, db_session, storage, monkeypatch
):
    body = upload(garment_client).json()

    def fail(_):
        raise OSError("file is busy")

    monkeypatch.setattr(storage, "delete_prefix", fail)
    assert garment_client.delete(f"/api/v1/garments/{body['garment_id']}").status_code == 204
    assert garment_client.get(f"/api/v1/garments/{body['garment_id']}").status_code == 404
    assert garment_client.get(body["image_url"]).status_code == 404
    job = db_session.get(StorageDeletionJob, UUID(body["garment_id"]))
    assert job.attempts == 1 and job.last_error == "OSError"
    assert storage.exists(body["image_url"].removeprefix("/media/"))


def test_upload_does_not_create_a_placeholder_user(garment_client, db_session):
    upload(garment_client)
    assert db_session.scalar(select(func.count()).select_from(User)) == 1
    assert db_session.get(User, UUID("00000000-0000-4000-8000-000000000001")) is None
