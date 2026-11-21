import re
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest

from backend.tests.api.test_style_references import confirm_reference, fixture_photo, upload_reference
from backend.tests.e2e.recommendation_flow import submit_recommendation


def hidden_back(html):
    return unescape(re.search(r'name="back" value="([^"]*)"', html)[1])


def parameters(url):
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def form_values(**extra):
    return {
        "date": "2026-11-12",
        "timezone": "Asia/Seoul",
        "start": "09:00",
        "end": "10:00",
        "purpose": "class",
        "environment": "indoor",
        "intensity": "normal",
        "priority": "high",
        **extra,
    }


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{"mentor": False}, {"mentor": True}], indirect=True)
def test_preserve_conditions_across_schedule_management(live_frontend):
    ui, api, _ = live_frontend
    email, password = f"navigation-{uuid4().hex}@example.com", "NavigationFixture123!"
    signed = api.post("/api/v1/auth/signup", json={"email": email, "password": password})
    assert signed.status_code == 201
    account = {"headers": {"Authorization": "Bearer " + signed.json()["access_token"]}}
    ids = []
    for category in ("top", "top", "bottom", "bottom", "outerwear"):
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
                    "style_candidates": ["casual", "minimal"],
                    "thermal_level": 5 if category == "outerwear" else 4,
                },
            ).status_code
            == 200
        )
        ids.append(gid)
    ref = confirm_reference(api, account, upload_reference(api, account))
    assert ui.post("/login", data={"email": email, "password": password}).status_code == 303
    pairs = [
        ("required", ids[0]),
        ("required", ids[-1]),
        ("exclude", ids[1]),
        ("exclude", ids[3]),
        ("styles", "casual"),
        ("styles", "minimal"),
        ("style_text", "빈티지 & 꾸안꾸"),
        ("temp", "-2.5"),
        ("occasion", "date"),
        ("outerwear_policy", "removable"),
        ("reference_id", ref["id"]),
        ("reference_version", str(ref["version"])),
    ]
    back = "/recommend?" + urlencode(pairs)
    expected = parameters(back)
    original = ui.get(back)
    assert original.status_code == 200
    # Server-rendered links also preserve loaded values when JS is unavailable.
    manage = unescape(re.search(r'id="schedule-manage" href="([^"]*)"', original.text)[1])
    assert parameters(parameters(manage)["back"][0]) == expected
    page = ui.get(manage)
    assert page.status_code == 200
    assert parameters(hidden_back(page.text)) == expected
    cancel = unescape(re.search(r'<a href="([^"]*)">저장하지 않고', page.text)[1])
    assert parameters(cancel) == expected
    bad = ui.post("/day-plans/save", data=form_values(back=hidden_back(page.text), end="08:00"))
    assert bad.status_code == 422 and parameters(hidden_back(bad.text)) == expected
    saved = ui.post("/day-plans/save", data=form_values(back=hidden_back(bad.text)))
    assert saved.status_code == 303
    selected = saved.headers["location"]
    fields = parameters(selected)
    pid = fields["schedule_id"][0]
    assert {k: v for k, v in fields.items() if not k.startswith("schedule_")} == expected
    assert fields["schedule_version"] == ["1"]
    restored = ui.get(selected)
    assert restored.status_code == 200
    assert 'value="-2.5"' in restored.text and 'name="occasion" value="date" checked' in restored.text
    for style in ("casual", "minimal"):
        assert re.search(rf'<input\b[^>]*name="styles"[^>]*value="{style}"[^>]*\bchecked\b', restored.text)
    assert f'value="{ref["id"]}" data-version="2" selected' in restored.text
    for gid in (ids[0], ids[-1]):
        assert f'value="{gid}" selected' in restored.text
    listing = ui.get("/day-plans", params={"back": selected})
    edit_url = unescape(re.search(r'<a href="([^"]*)">수정</a>', listing.text)[1])
    edit = ui.get(edit_url)
    assert edit.status_code == 200 and parameters(hidden_back(edit.text)) == fields
    assert parameters(unescape(re.search(r'<a href="([^"]*)">코디 추천</a>', listing.text)[1])) == fields
    changed = ui.post(
        "/day-plans/save",
        data=form_values(back=hidden_back(edit.text), id=pid, version=1, purpose="presentation"),
    )
    assert changed.status_code == 303
    current = changed.headers["location"]
    assert parameters(current) == {**fields, "schedule_version": ["2"]}
    # Stale editor and invalid delete preserve all other conditions for recovery.
    stale = ui.post("/day-plans/save", data=form_values(back=selected, id=pid, version=1))
    assert stale.status_code == 409 and parameters(hidden_back(stale.text)) == fields
    failed_delete = ui.post("/day-plans/delete", data={"id": pid, "version": 1, "back": current})
    assert failed_delete.status_code == 409 and parameters(hidden_back(failed_delete.text)) == parameters(
        current
    )
    # A second tab has its own conditions; there is no shared draft cookie/global state.
    second_back = "/recommend?temp=27&styles=sporty"
    second = ui.post("/day-plans/save", data=form_values(back=second_back))
    other_pid = parameters(second.headers["location"])["schedule_id"][0]
    assert parameters(hidden_back(ui.get("/day-plans", params={"back": current}).text)) == parameters(current)
    # Follow the restored form through the real API and verify the persisted context.
    result = submit_recommendation(ui, urlsplit(current).query)
    assert result.status_code == 200, result.text[:1000]
    records = api.get("/api/v1/recommendations", headers=account["headers"]).json()
    record = next(r for r in records if r["context"].get("schedule_id") == pid)
    context = record["context"]
    assert context["temperature_c"] == -2.5 and context["occasion"] == "date"
    assert {ids[0], ids[-1]} == set(context["required_garment_ids"])
    assert {ids[1], ids[3]} == set(context["excluded_items"])
    assert {"casual", "minimal", "빈티지 & 꾸안꾸"} <= set(context["preferred_styles"])
    assert context["reference_id"] == ref["id"] and context["reference_version"] == 2
    assert context["schedule_version"] == 2 and context["outerwear_policy"] == "removable"
    assert all(
        {ids[0], ids[-1]} <= set(o["items"].values()) and o["reference_matches"] and o["wearing_plan"]
        for o in record["outfits"]
    )
    # Delete an unselected schedule: keep current choice; delete selected: clear just its pair.
    deleted_other = ui.post("/day-plans/delete", data={"id": other_pid, "version": 1, "back": current})
    assert deleted_other.status_code == 303
    assert parameters(parameters(deleted_other.headers["location"])["back"][0]) == parameters(current)
    deleted = ui.post("/day-plans/delete", data={"id": pid, "version": 2, "back": current})
    assert deleted.status_code == 303
    after = parameters(deleted.headers["location"])["back"][0]
    assert parameters(after) == expected
    assert ui.get(after).status_code == 200
    # An outside destination supplied as back never becomes a redirect or recovery link.
    external = ui.post("/day-plans/save", data=form_values(back="https://example.invalid/recommend?temp=5"))
    assert external.status_code == 303 and external.headers["location"].startswith("/recommend?")
    assert "temp" not in parameters(external.headers["location"])
