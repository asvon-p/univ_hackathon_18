"""Only one owned wardrobe may enter the GPU queue; history stays untouched."""

from uuid import UUID

import pytest
from sqlalchemy import func, select

from backend.app.db.models import Garment, TryOnAdmission, TryOnResult
from backend.tests.api.test_tryon_results import RESULTS, finish, prepare


def mark_example(db, gid):
    db.get(Garment, UUID(gid)).is_example = True
    db.commit()


@pytest.mark.parametrize("policy", ["persistent", "session"])
@pytest.mark.parametrize("scope", [None, "personal", "example"])
def test_mixed_selection_rejected_before_queue_or_result_writes(
    api_client, auth_account, add_garment, db_session, storage, policy, scope
):
    rid, top = prepare(api_client, auth_account, db_session, policy)
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    bottom = add_garment(auth_account, "bottom")
    mark_example(db_session, bottom)
    before = api_client.get(RESULTS, headers=h).json()
    photo = api_client.get(f"{RESULTS}/{rid}/image", headers=h).content
    count = db_session.scalar(select(func.count()).select_from(TryOnResult))
    body = {"items": {"top": top, "bottom": bottom}}
    if scope is not None:
        body["wardrobe"] = scope
    response = api_client.post(RESULTS, headers=h, json=body)
    assert response.status_code == 422 and response.json()["code"] == "TRYON_WARDROBE_MIXED"
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 0
    assert db_session.scalar(select(func.count()).select_from(TryOnResult)) == count
    assert api_client.get(RESULTS, headers=h).json() == before
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).content == photo


@pytest.mark.parametrize("policy", ["persistent", "session"])
@pytest.mark.parametrize("scope", ["personal", "example"])
@pytest.mark.parametrize("explicit", [False, True])
def test_same_wardrobe_all_three_slots_can_be_saved_and_read(
    api_client, auth_account, add_garment, db_session, policy, scope, explicit
):
    rid, top = prepare(api_client, auth_account, db_session, policy)
    h = auth_account["headers"]
    assert api_client.post(f"{RESULTS}/{rid}/cancel", headers=h).status_code == 204
    items = {"top": top, **{slot: add_garment(auth_account, slot) for slot in ("bottom", "outerwear")}}
    if scope == "example":
        for gid in items.values():
            mark_example(db_session, gid)
    body = {"items": items, **({"wardrobe": scope} if explicit else {})}
    created = api_client.post(RESULTS, headers=h, json=body)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert created.json()["items"] == items
    assert finish(api_client, h, rid).status_code == 200
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).status_code == 200


@pytest.mark.parametrize("scope", ["personal", "example"])
def test_claimed_scope_cannot_override_database_scope(api_client, auth_account, db_session, scope):
    rid, top = prepare(api_client, auth_account, db_session)
    h = auth_account["headers"]
    assert api_client.post(f"{RESULTS}/{rid}/cancel", headers=h).status_code == 204
    if scope == "personal":
        mark_example(db_session, top)
    response = api_client.post(RESULTS, headers=h, json={"items": {"top": top}, "wardrobe": scope})
    assert response.status_code == 422 and response.json()["code"] == "TRYON_WARDROBE_MISMATCH"
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 0
    for invalid in ("all", "auto", "invalid"):
        assert (
            api_client.post(RESULTS, headers=h, json={"items": {"top": top}, "wardrobe": invalid}).status_code
            == 422
        )


def test_foreign_or_wrong_role_selection_is_rejected_without_scope_disclosure(
    api_client, auth_account, account_factory, add_garment, db_session
):
    rid, top = prepare(api_client, auth_account, db_session)
    h = auth_account["headers"]
    assert api_client.post(f"{RESULTS}/{rid}/cancel", headers=h).status_code == 204
    other = account_factory()
    foreign = add_garment(other, "bottom")
    mark_example(db_session, foreign)
    for items in ({"top": top, "bottom": foreign}, {"bottom": top}):
        response = api_client.post(RESULTS, headers=h, json={"items": items, "wardrobe": "personal"})
        assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 0


def test_legacy_mixed_result_is_readable_but_cannot_be_submitted_again(
    api_client, auth_account, add_garment, db_session
):
    rid, top = prepare(api_client, auth_account, db_session)
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    bottom = add_garment(auth_account, "bottom")
    mark_example(db_session, bottom)
    legacy = db_session.get(TryOnResult, UUID(rid))
    legacy.items = {"top": top, "bottom": bottom}
    db_session.commit()
    before = api_client.get(f"{RESULTS}/{rid}/image", headers=h).content
    assert api_client.post(RESULTS, headers=h, json={"items": legacy.items}).status_code == 422
    assert api_client.get(f"{RESULTS}/{rid}", headers=h).json()["items"] == legacy.items
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).content == before
