import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import pytest

from backend.tests.api.test_day_plans import create_plan
from backend.tests.api.test_style_references import confirm_reference, fixture_photo, upload_reference
from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{"mentor": False}, {"mentor": True}], indirect=True)
def test_reference_screen_with_schedule_required_and_concurrent_owners(live_frontend):
    ui, api, _ = live_frontend
    accounts = []
    for _ in range(2):
        email = f"reference-{uuid4().hex}@example.com"
        response = api.post("/api/v1/auth/signup", json={"email": email, "password": "ReferenceTest123!"})
        assert response.status_code == 201, response.text
        account = {"headers": {"Authorization": "Bearer " + response.json()["access_token"]}}
        ids = []
        for category in ("top", "bottom", "outerwear"):
            uploaded = api.post(
                "/api/v1/garments",
                headers=account["headers"],
                files={"file": ("fixture.png", fixture_photo(), "image/png")},
            )
            assert uploaded.status_code == 201
            gid = uploaded.json()["garment_id"]
            assert (
                api.patch(
                    "/api/v1/garments/" + gid,
                    headers=account["headers"],
                    json={
                        "category": category,
                        "color": "red",
                        "style_candidates": ["minimal"],
                        "thermal_level": 3,
                    },
                ).status_code
                == 200
            )
            ids.append(gid)
        plan = create_plan(api, account)
        accounts.append((email, account, ids, plan))
    email, owner, ids, plan = accounts[0]
    _, other, other_ids, other_plan = accounts[1]
    assert ui.post("/login", data={"email": email, "password": "ReferenceTest123!"}).status_code == 303
    back = "/recommend?" + urlencode(
        {
            "temp": 35,
            "required": ids[0],
            "schedule_id": plan["id"],
            "schedule_version": 1,
            "outerwear_policy": "removable",
        }
    )
    uploaded = ui.post(
        "/style-references/upload",
        data={"back": back},
        files={"file": ("style.png", fixture_photo(), "image/png")},
    )
    assert uploaded.status_code == 303, uploaded.text
    detail = uploaded.headers["location"]
    rid = urlparse(detail).path.rsplit("/", 1)[-1]
    screen = ui.get(detail)
    assert screen.status_code == 200 and "사진은 일회성" in screen.text and "최대 30분" in screen.text
    image = ui.get(f"/style-references/{rid}/image")
    assert image.status_code == 200 and image.headers["cache-control"] == "private, no-store"
    analysis = ui.post(
        f"/style-references/{rid}/analyze",
        data={
            "version": 1,
            "back": back,
            "regions_json": json.dumps(
                [
                    {"category": "top", "x": 0, "y": 0, "width": 1, "height": 0.5},
                    {"category": "bottom", "x": 0, "y": 0.5, "width": 1, "height": 0.5},
                ]
            ),
        },
    )
    assert analysis.status_code == 303, analysis.text
    assert "픽셀 색상 추정" in ui.get(detail).text
    assert (
        "원본 보관 종료" in ui.get(detail).text and '<canvas id="reference-canvas"' not in ui.get(detail).text
    )
    assert ui.get(f"/style-references/{rid}/image").status_code == 404
    once = upload_reference(api, owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(
            pool.map(
                lambda _: api.post(
                    f"/api/v1/style-references/{once['id']}/analyze",
                    headers=owner["headers"],
                    json={
                        "expected_version": 1,
                        "region": {"category": "top", "x": 0, "y": 0, "width": 1, "height": 0.5},
                    },
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in attempts) == [200, 409]
    confirmed = ui.post(
        f"/style-references/{rid}/confirm",
        data={
            "version": 2,
            "back": back,
            "include": "top",
            "top_color": "red",
            "top_pattern": "solid",
            "top_styles": "minimal",
            "focus": "color",
        },
    )
    assert confirmed.status_code == 303, confirmed.text
    params = {k: v[0] for k, v in parse_qs(urlparse(confirmed.headers["location"]).query).items()}
    assert params["required"] == ids[0] and params["schedule_id"] == plan["id"]
    assert params["reference_id"] == rid and params["reference_version"] == "3"
    form = ui.get(confirmed.headers["location"])
    assert form.status_code == 200
    result = submit_recommendation(ui, params)
    assert result.status_code == 200, result.text[:1500]
    assert "참고 사진과 비교" in result.text and "하루 착용 안내" in result.text
    assert "필수 포함" in result.text
    other_ref = confirm_reference(api, other, upload_reference(api, other))

    def request_for(account, selected, schedule, reference, version):
        return api.post(
            "/api/v1/recommendations",
            headers=account["headers"],
            json={
                "temperature_c": 35,
                "required_garment_ids": selected,
                "outerwear_policy": "removable",
                "schedule_id": schedule["id"],
                "schedule_version": 1,
                "reference_id": reference,
                "reference_version": version,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda args: request_for(*args),
                [(owner, ids, plan, rid, 3), (other, other_ids, other_plan, other_ref["id"], 2)],
            )
        )
    for response, required, denied in ((responses[0], ids, other), (responses[1], other_ids, owner)):
        assert response.status_code == 201, response.text
        data = response.json()
        assert all(
            set(required) <= set(o["items"].values())
            and o["reference_matches"]
            and len(o["wearing_plan"]) == 2
            for o in data["outfits"]
        )
        assert (
            api.get(
                "/api/v1/recommendations/" + data["recommendation_id"], headers=denied["headers"]
            ).status_code
            == 404
        )
    assert request_for(other, other_ids, other_plan, rid, 3).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as pool:
        updates = list(
            pool.map(
                lambda _: api.patch(
                    "/api/v1/style-references/" + rid,
                    headers=owner["headers"],
                    json={"expected_version": 3, "features": [{"category": "top", "color": "blue"}]},
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in updates) == [200, 409]
    assert ui.post(f"/style-references/{rid}/delete", data={"version": 4}).status_code == 303
    assert api.get("/api/v1/style-references/" + rid, headers=owner["headers"]).status_code == 404
    assert api.get("/api/v1/day-plans/" + plan["id"], headers=owner["headers"]).status_code == 200
