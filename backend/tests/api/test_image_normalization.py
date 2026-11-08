from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import func, select

from backend.app.adapters.vision import LocalVision, SafeVision
from backend.app.db.models import Garment
from backend.app.deps import get_vision
from backend.tests.test_image_normalization import heic_photo


def test_preview_requires_active_user_and_writes_nothing(api_client, auth_account, db_session, storage):
    path = "/api/v1/images/normalize"
    files = {"file": ("iphone.HEIC", heic_photo(), "application/octet-stream")}
    assert api_client.post(path, files=files).status_code == 401
    result = api_client.post(path, files=files, headers=auth_account["headers"])
    assert result.status_code == 200 and result.headers["content-type"] == "image/jpeg"
    assert result.headers["cache-control"] == "private, no-store"
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 0
    assert not list(storage.root.rglob("*.jpg"))
    assert api_client.delete("/api/v1/auth/me", headers=auth_account["headers"]).status_code == 204
    assert api_client.post(path, files=files, headers=auth_account["headers"]).status_code == 401


@pytest.mark.parametrize("kind", ["garment", "body"])
def test_heic_upload_is_standardized_and_private(api_client, auth_account, account_factory, kind):
    headers = auth_account["headers"]
    other = account_factory()["headers"]
    files = {"file": ("photo.txt", heic_photo(), "text/plain")}
    if kind == "body":
        assert (
            api_client.patch("/api/v1/profile", headers=headers, json={"consent_body_info": True}).status_code
            == 200
        )
        result = api_client.put("/api/v1/users/me/body-photo", headers=headers, files=files)
        path = "/api/v1/users/me/body-photo"
        assert result.status_code == 200
    else:
        result = api_client.post("/api/v1/garments", headers=headers, files=files)
        assert result.status_code == 201
        path = result.json()["image_url"]
    image = api_client.get(path, headers=headers)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(image.content)) as decoded:
        assert decoded.size == (240, 360) and not decoded.getexif()
    assert api_client.get(path, headers=other).status_code == 404


def test_teammate_pipeline_cutout_and_reanalysis_preserve_manual_fields(garment_client, db_session):
    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(
        LocalVision(classifier="demo", segmentation="flat", device="cpu", demo_category="bottom"), 10
    )
    image = Image.new("RGB", (600, 600), "white")
    ImageDraw.Draw(image).rectangle((180, 100, 420, 500), fill="#c03030")
    raw = BytesIO()
    image.save(raw, "HEIF")
    result = garment_client.post(
        "/api/v1/garments", files={"file": ("photo.heic", raw.getvalue(), "image/heic")}
    )
    assert result.status_code == 201
    row = result.json()
    assert row["analysis_status"] == "done" and row["category"] == "bottom" and row["color"] == "red"
    assert row["model_version"].startswith("vision-baseline-")
    assert row["cutout_url"]
    with Image.open(BytesIO(garment_client.get(row["cutout_url"]).content)) as cutout:
        assert cutout.size == (600, 600) and cutout.mode == "RGBA"
        assert cutout.getpixel((0, 0))[3] == 0
    path = "/api/v1/garments/" + row["garment_id"]
    garment_client.patch(path, json={"category": "outerwear", "color": "navy", "thermal_level": 4})
    retried = garment_client.post(path + "/reanalyze").json()
    assert retried["category"] == "outerwear" and retried["color"] == "navy" and retried["thermal_level"] == 4
    assert retried["cutout_path"] != row["cutout_path"]
    assert db_session.get(Garment, UUID(row["garment_id"])).attributes.category == "outerwear"
