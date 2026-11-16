from contextlib import nullcontext
from io import BytesIO
from uuid import UUID

import httpx
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from backend.app.db.models import ApiUsage, Garment, TryOnResult, User
from backend.app.services import api_usage
from backend.tests.api.test_body_photo import PATH, picture
from backend.tests.api.test_tryon_results import RESULTS, prepare
from shared.garment_layers import refined_layer


def test_admin_grant_revocation_and_stage_purge(
    api_client, auth_account, account_factory, db_session, storage
):
    rid, gid = prepare(api_client, auth_account, db_session)
    owner = auth_account["headers"]
    admin = account_factory()
    h = admin["headers"]
    routes = [
        "/api/v1/admin/overview",
        "/api/v1/admin/artifacts",
        f"/api/v1/admin/media/garments/{gid}/original",
    ]
    for route in routes:
        assert api_client.get(route).status_code == 401
        assert api_client.get(route, headers=h).status_code == 403
    user = db_session.get(User, UUID(admin["id"]))
    user.is_admin = True
    db_session.commit()
    assert api_client.get("/api/v1/auth/me", headers=h).json()["is_admin"] is True
    for route in routes:
        response = api_client.get(route, headers=h)
        assert response.status_code == 200, response.text
        assert "no-store" in response.headers["cache-control"]
    finished = api_client.put(
        f"{RESULTS}/{rid}/image",
        headers=owner,
        files={
            "file": ("result.png", picture(), "image/png"),
            "stage_top": ("top.png", picture(), "image/png"),
        },
    )
    assert finished.status_code == 200, finished.text
    row = db_session.get(TryOnResult, UUID(rid))
    stage_key = f"{auth_account['id']}/tryon/{row.image_id}/top.jpg"
    assert storage.exists(stage_key)
    media = f"/api/v1/admin/media/results/{rid}/top"
    assert api_client.get(media, headers=owner).status_code == 403
    assert api_client.get(media, headers=h).status_code == 200
    assert api_client.get(f"/api/v1/admin/artifacts?owner={admin['id']}", headers=h).json()["results"] == []
    user.is_admin = False
    db_session.commit()
    assert api_client.get(media, headers=h).status_code == 403
    assert api_client.delete(PATH, headers=owner, params={"include_tryons": True}).status_code == 204
    assert not storage.exists(stage_key)
    assert api_client.get(f"/api/v1/garments/{gid}", headers=owner).status_code == 200


def test_signup_cannot_grant_admin(api_client):
    response = api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "no-escalation@example.com",
            "password": "test-password-123",
            "nickname": "test",
            "is_admin": True,
        },
    )
    assert response.status_code == 422


def test_opening_keeps_original_and_enforces_owner(
    api_client, auth_account, account_factory, db_session, storage
):
    h = auth_account["headers"]
    gid = api_client.post(
        "/api/v1/garments", headers=h, files={"file": ("outer.png", picture(), "image/png")}
    ).json()["garment_id"]
    path = f"/api/v1/garments/{gid}"
    api_client.patch(path, headers=h, json={"category": "outerwear"})
    row = db_session.get(Garment, UUID(gid))
    original = storage.open(row.original_path).read()
    # Seed a known cutout: this test is about user editing/PNG delivery, not rembg inference.
    im = Image.new("RGBA", (200, 300))
    ImageDraw.Draw(im).rectangle((20, 20, 180, 280), fill=(30, 60, 40, 255))
    key = f"{auth_account['id']}/{gid}/cutout.png"
    out = BytesIO()
    im.save(out, format="PNG")
    storage.save(key, out.getvalue())
    row.cutout_path = key
    db_session.commit()
    points = [[0.42, 0.12], [0.58, 0.12], [0.65, 0.9], [0.35, 0.9]]
    other = account_factory()["headers"]
    assert api_client.put(path + "/layer-config", headers=other, json={"points": points}).status_code == 404
    assert api_client.get(path + "/layer-image", headers=other).status_code == 404
    assert (
        api_client.put(path + "/layer-config", headers=h, json={"points": [[0, 0], [1, 1]]}).status_code
        == 422
    )
    assert api_client.put(path + "/layer-config", headers=h, json={"points": points}).status_code == 200
    response = api_client.get(path + "/layer-image", headers=h)
    assert response.status_code == 200
    result = Image.open(BytesIO(response.content))
    assert result.getpixel((100, 150))[3] == 0
    assert result.getpixel((50, 150))[3] == 255
    assert "fitjang_opening" in result.info
    assert result.info["fitjang_wearing_role"] == "outerwear"
    assert result.info["fitjang_outerwear_length"] == "long"
    assert storage.open(row.original_path).read() == original
    api_client.patch(path, headers=h, json={"category": "top"})
    assert api_client.put(path + "/layer-config", headers=h, json={"points": points}).status_code == 422


def test_usage_receipts_and_unknown_costs(db_session, auth_account, monkeypatch):
    monkeypatch.setattr(api_usage, "get_sessionmaker", lambda: lambda: nullcontext(db_session))
    rid = api_usage.begin_usage(auth_account["id"], "gpt-6-luna")
    api_usage.finish_usage(
        rid,
        httpx.Response(
            200,
            json={
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 200},
                }
            },
        ),
    )
    row = db_session.get(ApiUsage, rid)
    assert row.estimated_usd == pytest.approx(0.000132)
    assert row.status == "response_received"
    unknown = api_usage.begin_usage(auth_account["id"], "gpt-6-luna")
    api_usage.finish_usage(unknown)
    assert db_session.get(ApiUsage, unknown).estimated_usd is None
    assert db_session.get(ApiUsage, unknown).status == "network_unknown"
    error = api_usage.begin_usage(auth_account["id"], "gpt-6-luna")
    api_usage.finish_usage(error, httpx.Response(500, text="upstream error"))
    assert db_session.get(ApiUsage, error).status == "http_error"
    assert len(db_session.scalars(select(ApiUsage)).all()) == 3


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"input_tokens": True, "output_tokens": 3},
        {"input_tokens": 100, "output_tokens": 3, "input_tokens_details": {"cached_tokens": "4"}},
        {"input_tokens": 3, "output_tokens": -1},
    ],
)
def test_unknown_usage_is_not_zero(usage):
    assert api_usage.estimate("gpt-6-luna", usage) is None


def test_refinement_preserves_material_and_removes_speckles():
    im = Image.new("RGBA", (100, 100), "white")
    im.putalpha(0)
    draw = ImageDraw.Draw(im)
    draw.rectangle((20, 20, 80, 80), fill=(20, 30, 40, 255))
    draw.point((5, 5), fill=(255, 255, 255, 255))
    out = Image.open(BytesIO(refined_layer(im)))
    assert out.getpixel((5, 5))[3] == 0
    assert out.getpixel((50, 50)) == (20, 30, 40, 255)
    assert 0 < out.getpixel((20, 50))[3] < 255
