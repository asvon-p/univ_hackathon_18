from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from backend.tests.api.test_day_plans import create_plan
from backend.tests.api.test_style_references import confirm_reference, fixture_photo, upload_reference
from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize(
    "live_api",
    [
        {"provider": "local", "openai_stub": "success"},
        {"provider": "local", "openai_stub": "failure"},
    ],
    indirect=True,
)
@pytest.mark.parametrize("live_frontend", [{"mentor": False}, {"mentor": True}], indirect=True)
def test_rendered_judgment_and_fallback_use_real_postgres(live_frontend, request):
    ui, api, runtime = live_frontend
    failed = request.node.callspec.params["live_api"]["openai_stub"] == "failure"
    email, password = f"judge-e2e-{uuid4().hex}@example.com", "JudgmentFixture123!"
    signup = api.post("/api/v1/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    account = {"headers": {"Authorization": "Bearer " + signup.json()["access_token"]}}
    ids = []
    for category, color in [
        ("top", "white"),
        ("top", "navy"),
        ("bottom", "black"),
        ("bottom", "beige"),
        ("outerwear", "blue"),
    ]:
        uploaded = api.post(
            "/api/v1/garments",
            headers=account["headers"],
            files={"file": ("fixture.png", fixture_photo(), "image/png")},
        )
        assert uploaded.status_code == 201
        gid = uploaded.json()["garment_id"]
        changed = api.patch(
            "/api/v1/garments/" + gid,
            headers=account["headers"],
            json={
                "category": category,
                "color": color,
                "style_candidates": ["casual", "minimal"],
                "thermal_level": 3,
            },
        )
        assert changed.status_code == 200
        ids.append(gid)
    plan = create_plan(api, account)
    ref = confirm_reference(api, account, upload_reference(api, account))
    assert ui.post("/login", data={"email": email, "password": password}).status_code == 303
    response = submit_recommendation(
        ui,
        {
            "temp": "7.5",
            "styles": "minimal",
            "required": ids[-1],
            "reference_id": ref["id"],
            "reference_version": ref["version"],
            "schedule_id": plan["id"],
            "schedule_version": 1,
            "outerwear_policy": "removable",
        },
    )
    assert response.status_code == 200, response.text
    assert (
        "자동 판단을 사용할 수 없어" if failed else "AI가 검증된 코디 후보의 순서와 이유"
    ) in response.text
    assert "하루 착용 안내" in response.text and "참고 사진과 비교" in response.text
    data = api.get("/api/v1/recommendations", headers=account["headers"]).json()[0]
    assert data["provider"] == ("fallback" if failed else "openai") and len(data["outfits"]) == 3
    assert all(
        o["items"]["outerwear"] == ids[-1] and o["reference_matches"] and len(o["wearing_plan"]) == 2
        for o in data["outfits"]
    )
    if not failed:
        assert [o["reason"] for o in data["outfits"]] == ["후보 판단 2", "후보 판단 1", "후보 판단 0"]
        assert (
            response.text.index("후보 판단 2")
            < response.text.index("후보 판단 1")
            < response.text.index("후보 판단 0")
        )
    engine = create_engine(runtime["database_url"])
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT o.reason, o.rank FROM recommendation_outfits o ORDER BY o.rank")
        ).all()
        assert [row.reason for row in rows] == [o["reason"] for o in data["outfits"]]
        assert [row.rank for row in rows] == [1, 2, 3]
    engine.dispose()
