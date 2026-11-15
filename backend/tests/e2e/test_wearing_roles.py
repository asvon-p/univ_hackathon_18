"""Local/mentor UI HTTP, real vision adapter and isolated PostgreSQL."""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from backend.tests.e2e.recommendation_flow import submit_recommendation
from backend.tests.e2e.test_long_garment_photo import photo


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{}, {"mentor": True}], indirect=True)
def test_outer_shirt_upload_confirm_reanalyze_recommend_and_isolation(live_frontend):
    ui, backend, runtime = live_frontend
    signup = ui.post(
        "/signup",
        data={
            "email": f"wearing-role-{uuid4().hex}@example.com",
            "password": "RoleE2E2026!",
            "password_confirm": "RoleE2E2026!",
        },
    )
    assert signup.status_code == 303
    h = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    form = ui.get("/garments/new")
    assert 'name="wearing_role"' in form.text and "티셔츠 위에 걸치는 셔츠" in form.text
    invalid = ui.post("/garments/new", data={"wearing_role": "outerwear"}, files=photo((300, 450)))
    assert invalid.status_code == 400 and 'value="outerwear" checked' in invalid.text
    ids = {}
    for role, shape in [("top", "t_shirt"), ("bottom", "jeans"), ("outerwear", "shirt")]:
        uploaded = ui.post("/garments/new", data={"wearing_role": role}, files=photo((300, 300)))
        assert uploaded.status_code == 303, uploaded.text
        path = uploaded.headers["location"]
        gid = ids[role] = path.split("/")[2]
        g = backend.get(f"/api/v1/garments/{gid}", headers=h).json()
        assert g["wearing_role"] == role and g["role_source"] == "user"
        page = ui.get(path)
        assert "data-category=" not in page.text and "착용 역할" in page.text and "옷 형태" in page.text
        confirmed = ui.post(
            path,
            data={
                "category": role,
                "subcategory": shape,
                "color": "white",
                "pattern": "solid",
                "thermal_level": "2",
                "styles": "minimal",
            },
        )
        assert confirmed.status_code == 303, confirmed.text
        retry = backend.post(f"/api/v1/garments/{gid}/reanalyze", headers=h)
        assert retry.status_code == 200 and retry.json()["wearing_role"] == role
        assert retry.json()["garment_type"] == shape
    outer = ids["outerwear"]
    edit = ui.get(f"/garments/{outer}/edit")
    assert '<option value="shirt" selected>' in edit.text
    detail = ui.get(f"/garments/{outer}")
    assert "착용 역할: 아우터 · 옷 형태: 셔츠" in detail.text
    result = submit_recommendation(ui, {"temp": "18", "required": outer})
    assert result.status_code == 200 and "셔츠" in result.text
    saved = backend.get("/api/v1/recommendations", headers=h).json()[0]
    assert saved["provider"] == "local"
    assert all(o["items"]["outerwear"] == outer and o["items"]["top"] == ids["top"] for o in saved["outfits"])
    with httpx.Client(base_url=ui.base_url, trust_env=False) as other:
        assert (
            other.post(
                "/signup",
                data={
                    "email": f"role-other-{uuid4().hex}@example.com",
                    "password": "RoleE2E2026!",
                    "password_confirm": "RoleE2E2026!",
                },
            ).status_code
            == 303
        )
        assert other.get(f"/garments/{outer}/edit").status_code == 404
        assert other.get(f"/garment-images/{outer}").status_code == 404
    engine = create_engine(runtime["database_url"])
    try:
        with engine.connect() as db:
            row = db.execute(
                text(
                    "SELECT category, subcategory, user_edited_fields FROM garment_attributes "
                    "WHERE garment_id=:gid"
                ),
                {"gid": outer},
            ).one()
            assert row.category == "outerwear" and row.subcategory == "shirt"
            assert {"category", "subcategory"} <= set(row.user_edited_fields)
    finally:
        engine.dispose()
