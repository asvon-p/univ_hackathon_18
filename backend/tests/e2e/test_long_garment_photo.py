"""Long garment uploads keep their full height through real UI HTTP and PostgreSQL."""

from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, text


def photo(size):
    im = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(im)
    draw.rectangle((10, 10, size[0] - 10, 45), fill="red")
    draw.rectangle((10, size[1] - 45, size[0] - 10, size[1] - 10), fill="blue")
    output = BytesIO()
    im.save(output, "JPEG", quality=95)
    return {"file": ("long-garment.jpg", output.getvalue(), "image/jpeg")}


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{}, {"mentor": True}], indirect=True)
def test_long_mode_ratio_validation_storage_and_isolation(live_frontend):
    ui, backend, runtime = live_frontend
    password = "LongGarmentTest2026!"
    assert (
        ui.post(
            "/signup",
            data={
                "email": f"long-{uuid4().hex}@example.com",
                "password": password,
                "password_confirm": password,
            },
        ).status_code
        == 303
    )
    headers = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    page = ui.get("/garments/new")
    assert page.status_code == 200 and "롱패딩·긴 코트 등" in page.text
    assert page.headers["permissions-policy"].split(",")[0] == "camera=(self)"
    for mode, size in [("square", (600, 900)), ("long", (600, 600)), ("long", (900, 600))]:
        response = ui.post("/garments/new", data={"photo_mode": mode}, files=photo(size))
        assert response.status_code == 400
        assert "틀에 맞춰" in response.text
        if mode == "long":
            assert 'value="long" checked' in response.text
    assert (
        ui.post("/garments/new", data={"photo_mode": "unknown"}, files=photo((600, 900))).status_code == 422
    )
    assert backend.get("/api/v1/garments", headers=headers).json() == []
    long = ui.post("/garments/new", data={"photo_mode": "long"}, files=photo((600, 900)))
    assert long.status_code == 303
    gid = long.headers["location"].split("/")[2]
    raw = backend.get(f"/api/v1/garments/{gid}/image?kind=original", headers=headers)
    assert raw.status_code == 200
    with Image.open(BytesIO(raw.content)) as im:
        assert im.size == (600, 900)
        assert im.getpixel((300, 25))[0] > 230
        assert im.getpixel((300, 875))[2] > 230
    assert ui.get(long.headers["location"]).status_code == 200
    assert ui.post("/garments/new", files=photo((600, 600))).status_code == 303
    with httpx.Client(base_url=ui.base_url, trust_env=False) as other:
        assert (
            other.post(
                "/signup",
                data={
                    "email": f"other-{uuid4().hex}@example.com",
                    "password": password,
                    "password_confirm": password,
                },
            ).status_code
            == 303
        )
        assert other.get(f"/garment-images/{gid}").status_code == 404
    engine = create_engine(runtime["database_url"])
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM garments")) == 2
    engine.dispose()
