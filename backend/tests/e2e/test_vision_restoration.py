import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, ImageOps


@pytest.mark.skipif(not os.getenv("VISION_SMOKE_IMAGE"), reason="Explicit real CLIP/U2Net test")
@pytest.mark.parametrize(
    "live_api",
    [{"provider": "local", "classifier": "clip", "segmentation": "rembg", "vision_timeout": 60}],
    indirect=True,
)
@pytest.mark.parametrize("live_frontend", [{}, {"mentor": True}], indirect=True)
def test_real_three_categories_upload_through_screens(live_frontend):
    ui, api, runtime = live_frontend
    health = api.get("/health").json()
    assert health["status"] == "ok" and health["vision_error"] is None
    signup = ui.post(
        "/signup",
        data={
            "email": f"vision-restore-{uuid4().hex}@example.com",
            "password": "Vision2026!",
            "password_confirm": "Vision2026!",
        },
    )
    assert signup.status_code == 303
    headers = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    root = Path(__file__).resolve().parents[2] / "assets/starter_wardrobe"
    expected = [
        ("top-white-oxford", "top"),
        ("bottom-blue-jeans", "bottom"),
        ("outerwear-olive-bomber", "outerwear"),
    ]
    for name, category in expected:
        # The real browser cropper submits a square image for ordinary garments.
        content = BytesIO()
        with Image.open(root / (name + ".jpg")) as image:
            ImageOps.pad(image, (1024, 1024), color="white").save(content, "JPEG", quality=95)
        response = ui.post(
            "/garments/new",
            files={"file": ("photo.jpg", content.getvalue(), "image/jpeg")},
        )
        assert response.status_code == 303, response.text[:300]
        confirmation = ui.get(response.headers["location"])
        assert confirmation.status_code == 200
        rows = api.get("/api/v1/garments", headers=headers).json()
        garment_id = response.headers["location"].split("/")[2]
        row = next(g for g in rows if g["garment_id"] == garment_id)
        assert row["category"] == category and row["analysis_status"] == "done", row
        assert row["cutout_url"] and row["pattern"] != "unknown"
        assert row["style_candidates"] and 1 <= row["thermal_level"] <= 5
        assert row["model_version"].startswith("vision-baseline-0.3/clip/rembg")
        data = api.get(row["cutout_url"], headers=headers)
        with Image.open(BytesIO(data.content)) as cutout:
            assert cutout.mode == "RGBA" and cutout.getextrema()[3] == (0, 255)
        assert ui.get("/garments/" + row["garment_id"]).status_code == 200
    assert len(rows) == 3
