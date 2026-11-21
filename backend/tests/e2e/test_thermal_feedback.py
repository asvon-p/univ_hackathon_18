from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from PIL import Image

from backend.tests.e2e.recommendation_flow import submit_recommendation


class RegenerateForm(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.active = False
        self.values = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("id") == "regenerate-form"
        if self.active and tag == "input" and "name" in attrs:
            self.values.append((attrs["name"], attrs.get("value", "")))

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{}, {"mentor": True}], indirect=True)
def test_winter_feedback_regeneration_preserves_history_and_conditions(live_frontend):
    ui, api, _ = live_frontend
    signup = ui.post(
        "/signup",
        data={
            "email": f"thermal-{uuid4().hex}@example.com",
            "password": "Thermal2026!",
            "password_confirm": "Thermal2026!",
        },
    )
    assert signup.status_code == 303
    headers = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    image = BytesIO()
    Image.new("RGB", (32, 32), "navy").save(image, format="PNG")

    def add(role, level, subtype):
        res = api.post(
            "/api/v1/garments",
            headers=headers,
            files={"file": ("synthetic.png", image.getvalue(), "image/png")},
        )
        assert res.status_code == 201, res.text
        gid = res.json()["garment_id"]
        res = api.patch(
            f"/api/v1/garments/{gid}",
            headers=headers,
            json={
                "category": role,
                "subcategory": subtype,
                "thermal_level": level,
                "color": "navy",
                "style_candidates": ["minimal"],
            },
        )
        assert res.status_code == 200, res.text
        return gid

    tee = add("top", 1, "t-shirt")
    knit = add("top", 4, "sweater")
    pants = add("bottom", 4, "jeans")
    outer = add("outerwear", 5, "padding")
    first = submit_recommendation(
        ui, {"wardrobe": "personal", "temp": "-3", "required": [pants], "styles": ["minimal"]}
    )
    assert first.status_code == 200, first.text[:500]
    rid = first.url.path.rsplit("/", 1)[-1]
    old = api.get(f"/api/v1/recommendations/{rid}", headers=headers).json()
    assert old["outfits"][0]["items"] == {"top": knit, "bottom": pants, "outerwear": outer, "shoes": None}
    warm = add("top", 5, "sweater")
    feedback = ui.post(
        "/recommend/feedback",
        headers={"X-Requested-With": "fetch"},
        data={
            "recommendation_id": rid,
            "outfit_id": old["outfits"][0]["outfit_id"],
            "accepted": "0",
            "reason": "영하인데 너무 추울 것 같아요",
        },
    )
    assert feedback.status_code == 200 and feedback.json()["reason_codes"] == ["too_cold"]
    refreshed = ui.get(first.url.path)
    assert "피드백 반영해 다시 추천" in refreshed.text
    assert api.get(f"/api/v1/recommendations/{rid}", headers=headers).json() == old
    form = RegenerateForm(refreshed.text)
    assert ("temp", "-3.0") in form.values or ("temp", "-3") in form.values
    assert ("required", pants) in form.values and ("wardrobe", "personal") in form.values
    encoded = urlencode(form.values)
    new = ui.post(
        "/recommend", content=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert new.status_code == 303, new.text[:500]
    result = ui.get(new.headers["location"])
    assert result.status_code == 200 and 'value="0" aria-pressed="false"' in result.text
    next_id = new.headers["location"].rsplit("/", 1)[-1]
    updated = api.get(f"/api/v1/recommendations/{next_id}", headers=headers).json()
    assert next_id != rid
    assert all(o["items"]["top"] == warm and o["items"]["bottom"] == pants for o in updated["outfits"])
    assert updated["context"]["recent_feedback"][0]["temperature_c"] == -3
    duplicate = ui.post(
        "/recommend", content=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert duplicate.headers["location"] == new.headers["location"]
    assert len(api.get("/api/v1/recommendations", headers=headers).json()) == 2
    conflict = submit_recommendation(ui, {"wardrobe": "personal", "temp": "-3", "required": [tee]})
    assert (
        conflict.status_code == 422 and "상의 두꺼움 이상" in conflict.text and "매우 두꺼움" in conflict.text
    )
    assert len(api.get("/api/v1/garments", headers=headers).json()) == 5
