import json
from io import BytesIO
from uuid import UUID

from PIL import Image

from backend.app.db.models import Garment
from backend.tests.api.test_body_photo import picture
from backend.tests.test_automatic_opening import jacket, soft_open_shirt


def test_auto_default_manual_override_disable_and_owner(
    api_client, auth_account, account_factory, db_session, storage
):
    h = auth_account["headers"]
    gid = api_client.post(
        "/api/v1/garments", headers=h, files={"file": ("test.png", picture(), "image/png")}
    ).json()["garment_id"]
    path = "/api/v1/garments/" + gid
    api_client.patch(path, headers=h, json={"category": "outerwear"})
    row = db_session.get(Garment, UUID(gid))
    original = storage.open(row.original_path).read()
    key = f"{auth_account['id']}/{gid}/cutout.png"
    stream = BytesIO()
    jacket(lining=True).save(stream, format="PNG")
    storage.save(key, stream.getvalue())
    row.cutout_path = key
    db_session.commit()
    updated = row.updated_at
    config = api_client.get(path + "/layer-config", headers=h)
    assert config.status_code == 200 and "no-store" in config.headers["cache-control"]
    auto = config.json()
    assert auto["mode"] == "auto" and auto["status"] == "detected"
    assert row.opening_polygon == []  # GET computes; no overwrite or DB mutation.
    assert row.updated_at == updated
    png = Image.open(BytesIO(api_client.get(path + "/layer-image", headers=h).content))
    assert json.loads(png.info["fitjang_opening"]) == auto["points"]
    assert png.getpixel((120, 160))[3] == 0
    other = account_factory()["headers"]
    for suffix in ("/layer-config", "/layer-image"):
        assert api_client.get(path + suffix, headers=other).status_code == 404
    assert api_client.put(path + "/layer-config", headers=other, json={"mode": "off"}).status_code == 404
    manual = [[0.45, 0.2], [0.55, 0.2], [0.58, 0.8], [0.42, 0.8]]
    assert (
        api_client.put(
            path + "/layer-config", headers=h, json={"mode": "manual", "points": manual}
        ).status_code
        == 200
    )
    config = api_client.get(path + "/layer-config", headers=h).json()
    assert config["status"] == "manual" and config["points"] == manual
    assert api_client.put(path + "/layer-config", headers=h, json={"mode": "auto"}).status_code == 200
    config = api_client.get(path + "/layer-config", headers=h).json()
    assert config["points"] == auto["points"] and config["manual_points"] == manual
    assert api_client.put(path + "/layer-config", headers=h, json={"mode": "off"}).status_code == 200
    png = Image.open(BytesIO(api_client.get(path + "/layer-image", headers=h).content))
    assert png.info["fitjang_opening_mode"] == "off" and png.getpixel((120, 160))[3] == 255
    assert (
        api_client.put(path + "/layer-config", headers=h, json={"mode": "auto", "points": manual}).status_code
        == 422
    )
    assert storage.open(row.original_path).read() == original


def test_outer_shirt_soft_cutout_repair_is_read_only_and_owner_only(
    api_client,
    auth_account,
    account_factory,
    db_session,
    storage,
):
    source, cutout = soft_open_shirt()
    original = BytesIO()
    source.save(original, format="PNG")
    h = auth_account["headers"]
    response = api_client.post(
        "/api/v1/garments",
        headers=h,
        data={"wearing_role": "outerwear", "garment_type": "shirt"},
        files={"file": ("synthetic-shirt.png", original.getvalue(), "image/png")},
    )
    assert response.status_code == 201
    gid = response.json()["garment_id"]
    row = db_session.get(Garment, UUID(gid))
    key = f"{auth_account['id']}/{gid}/soft-cutout.png"
    payload = BytesIO()
    cutout.save(payload, format="PNG")
    storage.save(key, payload.getvalue())
    row.cutout_path = key
    db_session.commit()
    original_bytes = storage.open(row.original_path).read()
    before = row.updated_at, row.attributes.user_edited_fields[:], row.opening_polygon[:]
    path = "/api/v1/garments/" + gid
    config = api_client.get(path + "/layer-config", headers=h).json()
    assert config["status"] == "detected" and config["detector"] == "product-opening-v2"
    image = api_client.get(path + "/layer-image", headers=h)
    assert image.status_code == 200 and "no-store" in image.headers["cache-control"]
    with Image.open(BytesIO(image.content)) as result:
        assert result.getpixel((120, 160))[3] == 0
        assert result.getpixel((65, 160))[3] == 255
        assert json.loads(result.info["fitjang_opening"]) == config["points"]
    db_session.expire_all()
    assert (row.updated_at, row.attributes.user_edited_fields, row.opening_polygon) == before
    assert row.attributes.category == "outerwear" and row.attributes.subcategory == "shirt"
    assert storage.open(row.original_path).read() == original_bytes
    assert storage.open(key).read() == payload.getvalue()
    other = account_factory()["headers"]
    for suffix in ("/layer-config", "/layer-image"):
        assert api_client.get(path + suffix, headers=other).status_code == 404
