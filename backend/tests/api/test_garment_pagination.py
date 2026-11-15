from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.app.db.models import Garment, GarmentAttribute
from backend.tests.api.test_recommendations import engine_result, recommend, use_engine

PATH = "/api/v1/garments/page"


def seed(db, account, count=65):
    rows = []
    for i in range(count):
        row = Garment(
            user_id=UUID(account["id"]),
            original_path="test/original.jpg",
            name=f"옷 {i}",
            location="기숙사",
            analysis_status="done",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            attributes=GarmentAttribute(
                category="top" if i % 2 else "bottom",
                subcategory="sweater" if i % 2 else "jeans",
                color="navy",
                pattern="solid",
                thermal_level=3,
                style_candidates=["minimal"],
            ),
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def test_keyset_ties_insert_delete_and_owner_scope(api_client, auth_account, account_factory, db_session):
    rows = seed(db_session, auth_account)
    other = account_factory()
    seed(db_session, other, 3)
    h = auth_account["headers"]
    first = api_client.get(PATH, headers=h).json()
    assert len(first["items"]) == 24 and first["total"] == first["all_count"] == 65
    assert first["counts"]["top"] == 32 and first["counts"]["bottom"] == 33
    seen = [g["garment_id"] for g in first["items"]]
    cursor = first["next_cursor"]
    assert api_client.get(PATH, params={"cursor": cursor}, headers=other["headers"]).status_code == 422
    assert api_client.get(PATH, params={"cursor": cursor, "category": "top"}, headers=h).status_code == 422
    # New upload above cursor and removal of a rendered item must not shift following pages.
    newest = seed(db_session, auth_account, 1)[0]
    newest.created_at = datetime.now(UTC)
    db_session.delete(next(r for r in rows if str(r.id) == seen[0]))
    db_session.commit()
    while cursor:
        page = api_client.get(PATH, params={"cursor": cursor}, headers=h).json()
        assert len(page["items"]) <= 24
        seen.extend(g["garment_id"] for g in page["items"])
        cursor = page["next_cursor"]
    assert len(seen) == len(set(seen)) == 65
    assert str(newest.id) not in seen
    assert set(seen) == {str(r.id) for r in rows}
    assert len(api_client.get(PATH, headers=other["headers"]).json()["items"]) == 3
    assert api_client.get(PATH).status_code == 401


def test_search_filter_counts_literals_and_empty(api_client, auth_account, db_session):
    rows = seed(db_session, auth_account, 6)
    rows[1].status = "laundry"
    rows[3].name = "100%_특별"
    db_session.commit()
    h = auth_account["headers"]
    p = api_client.get(PATH, params={"q": "남색 니트 미니멀", "status": "available"}, headers=h).json()
    assert p["total"] == 6 and p["all_count"] == p["matched_count"] == 2
    assert p["counts"]["top"] == 2 and p["counts"]["bottom"] == 0
    p = api_client.get(PATH, params={"q": "기숙사", "category": "bottom"}, headers=h).json()
    assert p["matched_count"] == 3 and p["all_count"] == 6
    assert len(api_client.get(PATH, params={"q": "%"}, headers=h).json()["items"]) == 1
    assert api_client.get(PATH, params={"q": "존재하지않음"}, headers=h).json()["items"] == []
    assert api_client.get(PATH, params={"q": "존재하지않음"}, headers=h).json()["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 61},
        {"cursor": "invalid"},
        {"cursor": "e30"},
        {"q": "x" * 51},
        {"category": "invalid"},
    ],
)
def test_invalid_page_input(api_client, auth_account, params):
    assert api_client.get(PATH, params=params, headers=auth_account["headers"]).status_code == 422


def test_recommendation_still_receives_entire_wardrobe(api_client, auth_account, db_session):
    rows = seed(db_session, auth_account)
    first = api_client.get(PATH, headers=auth_account["headers"]).json()
    displayed = {g["garment_id"] for g in first["items"]}
    top = next(r for r in rows if r.attributes.category == "top" and str(r.id) not in displayed)
    bottom = next(r for r in rows if r.attributes.category == "bottom" and str(r.id) not in displayed)
    received = []

    def judge(context, garments):
        received.extend(g.garment_id for g in garments)
        return engine_result({"top": str(top.id), "bottom": str(bottom.id)})

    use_engine(api_client, judge)
    result = recommend(api_client, auth_account, temperature_c=20, required_garment_ids=[str(top.id)])
    assert result.status_code == 201, result.text
    assert set(received) == {str(r.id) for r in rows}
    assert result.json()["outfits"][0]["items"]["top"] == str(top.id)
    assert len(api_client.get("/api/v1/garments", headers=auth_account["headers"]).json()) == 65
