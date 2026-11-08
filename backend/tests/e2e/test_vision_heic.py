"""Real HTTP and PostgreSQL, with HEIF bytes and the teammate's actual pipeline."""

import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from backend.tests.test_image_normalization import heic_photo


@pytest.mark.parametrize("live_api", [{"provider": "local", "invite_code": ""}], indirect=True)
@pytest.mark.parametrize(
    "live_frontend", [{"body_photo": True}, {"body_photo": True, "mentor": True}], indirect=True
)
def test_heic_crop_preview_upload_and_body_photo(live_frontend):
    ui, backend, runtime = live_frontend
    files = {"file": ("iphone.HEIC", heic_photo((600, 600)), "image/heic")}
    assert ui.post("/photos/normalize", files=files).status_code == 401
    email = f"heic-{uuid4().hex}@example.com"
    assert (
        ui.post(
            "/signup", data={"email": email, "password": "HeicPhoto123!", "password_confirm": "HeicPhoto123!"}
        ).status_code
        == 303
    )
    headers = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    for path in ["/garments/new", "/profile/body-photo"]:
        html = ui.get(path).text
        assert "image/heic,image/heif,.heic,.heif" in html
        assert "/photos/normalize" in html
    preview = ui.post("/photos/normalize", files=files)
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert preview.headers["cache-control"] == "private, no-store"
    assert backend.get("/api/v1/garments", headers=headers).json() == []
    assert backend.get("/api/v1/users/me/body-photo", headers=headers).status_code == 404
    assert (
        ui.post("/photos/normalize", files={"file": ("bad.heic", b"broken", "image/heic")}).status_code == 415
    )
    assert (
        ui.post(
            "/photos/normalize", files={"file": ("large.heic", b"x" * (10 * 1024 * 1024 + 1), "image/heic")}
        ).status_code
        == 413
    )
    uploaded = ui.post("/garments/new", files=files)
    assert uploaded.status_code == 303
    garment = backend.get("/api/v1/garments", headers=headers).json()[0]
    assert garment["analysis_status"] == "done" and garment["model_version"].startswith("vision-baseline-")
    stored = backend.get(garment["image_url"], headers=headers)
    with Image.open(BytesIO(stored.content)) as image:
        assert image.format == "JPEG" and not image.getexif()
    body = {"file": ("body.heif", heic_photo((600, 900)), "application/octet-stream")}
    assert ui.post("/profile/body-photo", files=body, data={"consent_body_photo": "1"}).status_code == 303
    assert ui.get("/body-photo").headers["content-type"] == "image/jpeg"
    engine = create_engine(runtime["database_url"])
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM garments")) == 1
        assert (
            connection.scalar(text("SELECT count(*) FROM user_profiles WHERE body_photo_id IS NOT NULL")) == 1
        )
    engine.dispose()
    with httpx.Client(base_url=ui.base_url, trust_env=False) as anonymous:
        assert anonymous.post("/photos/normalize", files=files).status_code == 401


@pytest.mark.skipif(
    not os.getenv("VISION_SMOKE_IMAGE"),
    reason="Set VISION_SMOKE_IMAGE to a solid blue denim jeans photo for real CLIP/U2Net validation",
)
@pytest.mark.parametrize(
    "live_api",
    [
        {
            "provider": "local",
            "invite_code": "",
            "classifier": "clip",
            "segmentation": "rembg",
            "vision_timeout": 60,
        }
    ],
    indirect=True,
)
def test_real_clip_u2net_heic_postgresql(live_api):
    client, _, runtime = live_api
    image_path = Path(os.environ["VISION_SMOKE_IMAGE"])
    raw = BytesIO()
    with Image.open(image_path) as image:
        image.save(raw, "HEIF", quality=90)
    email = f"vision-{uuid4().hex}@example.com"
    assert (
        client.post(
            "/api/v1/auth/signup", json={"email": email, "password": "Vision123!", "nickname": "Vision test"}
        ).status_code
        == 201
    )
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "Vision123!"}).json()[
        "access_token"
    ]
    headers = {"Authorization": "Bearer " + token}
    result = client.post(
        "/api/v1/garments", headers=headers, files={"file": ("iphone.HEIC", raw.getvalue(), "image/heic")}
    )
    assert result.status_code == 201
    row = result.json()
    assert row["analysis_status"] == "done" and row["category"] == "bottom", row
    assert "/clip/rembg" in row["model_version"] and row["cutout_url"]
    assert row["color"] == "denim" and row["pattern"] == "solid"
    assert 1 <= row["thermal_level"] <= 5 and 1 <= len(row["style_candidates"]) <= 3
    with Image.open(BytesIO(client.get(row["cutout_url"], headers=headers).content)) as cutout:
        assert cutout.mode == "RGBA" and cutout.getextrema()[3][0] == 0
    engine = create_engine(runtime["database_url"])
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT category FROM garment_attributes")) == "bottom"
        stored = (
            connection.execute(
                text(
                    "SELECT color, pattern, thermal_level, style_candidates, raw_output "
                    "FROM garment_attributes"
                )
            )
            .mappings()
            .one()
        )
        assert stored["color"] == "denim" and stored["pattern"] == "solid"
        assert stored["thermal_level"] == row["thermal_level"]
        assert stored["style_candidates"] == row["style_candidates"]
        assert stored["raw_output"]["result"]["attributes"]["pattern"] == "solid"
    engine.dispose()
    path = "/api/v1/garments/" + row["garment_id"]
    edits = {"pattern": "check", "style_candidates": [], "thermal_level": None, "color": "olive"}
    assert client.patch(path, headers=headers, json=edits).status_code == 200
    retry = client.post(path + "/reanalyze", headers=headers).json()
    assert retry["analysis_status"] == "done" and retry["source"] == "user"
    assert {k: retry[k] for k in edits} == edits
    other = f"other-{uuid4().hex}@example.com"
    assert (
        client.post("/api/v1/auth/signup", json={"email": other, "password": "OtherTest123!"}).status_code
        == 201
    )
    token2 = client.post("/api/v1/auth/login", json={"email": other, "password": "OtherTest123!"}).json()[
        "access_token"
    ]
    foreign = {"Authorization": "Bearer " + token2}
    assert client.get(path, headers=foreign).status_code == 404
    assert client.post(path + "/reanalyze", headers=foreign).status_code == 404
    assert client.get("/api/v1/garments", headers=foreign).json() == []
