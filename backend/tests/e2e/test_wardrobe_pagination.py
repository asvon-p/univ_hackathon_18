import html
import re
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.tests.api.test_garment_pagination import seed


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{}, {"mentor": True}], indirect=True)
def test_paginated_html_partial_search_and_auth(live_frontend):
    ui, backend, runtime = live_frontend
    r = ui.post(
        "/signup",
        data={
            "email": f"pages-{uuid4().hex}@example.com",
            "password": "Pages2026!",
            "password_confirm": "Pages2026!",
        },
    )
    assert r.status_code == 303
    account = backend.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    ).json()
    engine = create_engine(runtime["database_url"])
    with Session(engine) as db:
        rows = seed(db, account)
        rows[0].name = "<script>alert(1)</script>"
        db.commit()
    engine.dispose()
    page = ui.get("/wardrobe")
    assert page.status_code == 200
    assert page.text.count("data-garment-id=") == 24 and "65벌" in page.text
    seen = re.findall('data-garment-id="([^"]+)"', page.text)
    url = html.unescape(re.search('id="wardrobe-more"[^>]*href="([^"]+)"', page.text).group(1))
    # Cursor links also work without JavaScript.
    assert ui.get(url).text.count("data-garment-id=") == 24
    while url:
        r = ui.get(url + "&partial=true")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "private, no-store"
        fragment = r.json()["html"]
        assert "<script>" not in fragment
        seen.extend(re.findall('data-garment-id="([^"]+)"', fragment))
        url = r.json()["next_url"]
    assert len(seen) == len(set(seen)) == 65
    result = ui.get("/wardrobe", params={"q": "남색 니트", "category": "top", "status": "available"})
    assert result.status_code == 200 and "조건에 맞는 옷 32벌" in result.text
    assert ui.get("/wardrobe", params={"partial": True, "cursor": "bad"}).status_code == 422
    with httpx.Client(base_url=ui.base_url, trust_env=False) as stranger:
        assert stranger.get("/wardrobe?partial=true").status_code == 401
        assert stranger.get("/wardrobe").status_code == 303
