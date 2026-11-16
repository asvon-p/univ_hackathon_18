import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.tests.test_tryon_frontend import request
from frontend import avatar_preview as avatar
from frontend.connect import ApiError


@pytest.fixture
def start_setup(monkeypatch):
    monkeypatch.setattr(avatar.vton, "enabled", lambda: True)
    monkeypatch.setattr(avatar, "maintenance_error", lambda: None)
    monkeypatch.setattr(
        avatar.auth,
        "get_profile",
        AsyncMock(return_value=SimpleNamespace(user_id="owner", body_photo_updated_at="v1", saved_tryons=[])),
    )
    garments = {
        "t": SimpleNamespace(garment_id="t", category="top", name="상의", is_example=False),
        "b": SimpleNamespace(garment_id="b", category="bottom", name="하의", is_example=True),
    }

    async def get(_, gid):
        if gid not in garments:
            raise ApiError(404, "본인 의류가 아닙니다", "NOT_FOUND")
        return garments[gid]

    monkeypatch.setattr(avatar.closet, "get_garment", get)
    monkeypatch.setattr(avatar, "garment_name", lambda g: g.name)
    monkeypatch.setattr(avatar.closet, "may_have_photo", lambda g: True)
    post, gpu = AsyncMock(return_value={"id": "new"}), AsyncMock()
    monkeypatch.setattr(avatar, "api_request", post)
    monkeypatch.setattr(avatar, "_run", gpu)
    monkeypatch.setattr(avatar, "_TRYONS", {})
    monkeypatch.setattr(avatar, "_TASKS", set())
    return garments, post, gpu


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("missing_image", [False, True])
def test_mixed_selection_cannot_bypass_check_via_cache_or_skipped_photo(
    monkeypatch, start_setup, cached, missing_image
):
    _, post, gpu = start_setup
    if cached:
        monkeypatch.setattr(avatar, "tryon_state", lambda *args: {"id": "old-mixed", "status": "done"})
    if missing_image:
        monkeypatch.setattr(avatar.closet, "may_have_photo", lambda g: g.garment_id != "b")
    response = asyncio.run(avatar.tryon_start(request("token"), top="t", bottom="b"))
    assert response.status_code == 422 and b"TRYON_WARDROBE_MIXED" in response.body
    post.assert_not_awaited()
    gpu.assert_not_awaited()


@pytest.mark.parametrize("scope", ["personal", "example"])
def test_valid_selection_passes_derived_scope_to_backend(start_setup, scope):
    garments, post, gpu = start_setup
    garments["t"].is_example = scope == "example"

    async def start():
        result = await avatar.tryon_start(request("token"), top="t", wardrobe=scope)
        await asyncio.gather(*avatar._TASKS)
        return result

    result = asyncio.run(start())
    assert result["id"] == "new"
    assert post.call_args.kwargs["json"] == {"items": {"top": "t"}, "wardrobe": scope}
    gpu.assert_awaited_once()


def test_wrong_scope_foreign_and_wrong_role_are_not_silently_skipped(start_setup):
    _, post, gpu = start_setup
    for arguments, status in [
        ({"top": "t", "wardrobe": "example"}, 422),
        ({"top": "t", "bottom": "foreign"}, 404),
        ({"bottom": "t"}, 404),
    ]:
        assert asyncio.run(avatar.tryon_start(request("token"), **arguments)).status_code == status
    post.assert_not_awaited()
    gpu.assert_not_awaited()
