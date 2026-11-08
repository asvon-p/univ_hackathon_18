"""Ownership, photo revocation and sequential garment contract for D's try-on UI."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from frontend import avatar_preview as avatar
from frontend.connect import ApiError


def request(token):
    return Request({"type": "http", "headers": [(b"cookie", f"fitjang_token={token}".encode())]})


@pytest.mark.parametrize("state", ["owner", "other", "withdrawn", "photo_changed", "photo_deleted"])
def test_tryon_result_requires_live_owner_and_current_photo(monkeypatch, state):
    token = "owner-token"
    item = avatar.TryOn("job", avatar._owner(token), (avatar._owner(token), "v1"), [("top", "g", "top")])
    item.image, item.status = b"result", "done"
    monkeypatch.setattr(avatar, "_TRYONS", {item.id: item})
    profile = AsyncMock(
        return_value=SimpleNamespace(
            body_photo_updated_at={"photo_changed": "v2", "photo_deleted": None}.get(state, "v1")
        )
    )
    if state == "withdrawn":
        profile.side_effect = ApiError(401, "revoked")
    monkeypatch.setattr(avatar.auth, "get_profile", profile)
    response = asyncio.run(avatar.tryon_image(request("stranger" if state == "other" else token), item.id))
    assert response.status_code == (200 if state == "owner" else 404)
    if state == "owner":
        assert response.headers["cache-control"] == "private, no-store"


def test_sequential_bottom_top_outerwear_uses_previous_result(monkeypatch):
    item = avatar.TryOn("job", "owner", (), [(s, s, s) for s in avatar.ORDER])
    monkeypatch.setattr(avatar.auth, "fetch_body_photo", AsyncMock(return_value=(b"person", "image/jpeg")))
    monkeypatch.setattr(avatar.closet, "fetch_image", AsyncMock(return_value=(b"garment", "image/png")))
    submit = AsyncMock(side_effect=[{"job_id": str(i), "status": "done"} for i in range(3)])
    monkeypatch.setattr(avatar.vton, "submit", submit)
    monkeypatch.setattr(avatar.vton, "get_result", AsyncMock(side_effect=[b"bottom", b"top", b"outerwear"]))
    asyncio.run(avatar._run(item, "token"))
    assert item.status == "done" and item.image == b"outerwear"
    assert [call.args[2] for call in submit.call_args_list] == ["bottoms", "tops", "tops"]
    assert [call.args[0][0] for call in submit.call_args_list] == [b"person", b"bottom", b"top"]


def test_result_survives_same_user_logging_in_again(monkeypatch):
    item = avatar.TryOn("job", "user-id", ("user-id", "v1"), [("top", "g", "top")])
    item.image = b"result"
    monkeypatch.setattr(avatar, "_TRYONS", {item.id: item})
    monkeypatch.setattr(
        avatar.auth,
        "get_profile",
        AsyncMock(return_value=SimpleNamespace(user_id="user-id", body_photo_updated_at="v1")),
    )
    assert asyncio.run(avatar.tryon_image(request("fresh-token"), item.id)).status_code == 200
