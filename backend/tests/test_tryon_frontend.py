"""Ownership, photo revocation and sequential garment contract for D's try-on UI."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
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


@pytest.mark.parametrize("outer_shape", ["jacket", "shirt", "cardigan"])
def test_sequential_bottom_top_outerwear_uses_previous_result(monkeypatch, outer_shape):
    assert tuple(avatar.ORDER) == ("bottom", "top", "outerwear")
    item = avatar.TryOn(
        "job", "owner", (), [(s, s, outer_shape if s == "outerwear" else s) for s in avatar.ORDER]
    )
    monkeypatch.setattr(avatar.auth, "fetch_body_photo", AsyncMock(return_value=(b"person", "image/jpeg")))
    monkeypatch.setattr("frontend.connect.request_bytes", AsyncMock(return_value=(b"garment", "image/png")))
    submit = AsyncMock(
        side_effect=[
            {"job_id": str(i), "status": "done", "layering": {"status": "applied"}} for i in range(3)
        ]
    )
    monkeypatch.setattr(avatar.vton, "submit", submit)
    monkeypatch.setattr(avatar.vton, "get_result", AsyncMock(side_effect=[b"bottom", b"top", b"outerwear"]))
    asyncio.run(avatar._run(item, "token"))
    assert item.status == "done" and item.image == b"outerwear"
    assert [call.args[2] for call in submit.call_args_list] == ["bottoms", "tops", "tops"]
    assert [call.args[0][0] for call in submit.call_args_list] == [b"person", b"bottom", b"top"]
    assert [call.kwargs["remaining_categories"] for call in submit.call_args_list] == [
        ("tops", "outerwear"),
        ("outerwear",),
        (),
    ]
    assert [call.kwargs["preserve_inner"] for call in submit.call_args_list] == [False, False, True]
    assert item.to_json()["layering_status"] == "applied"


def test_frontend_displays_gpu_eta_without_fixed_countdown():
    item = avatar.TryOn("job", "owner", (), [("top", "g", "top")])
    assert item.to_json()["seconds_left"] is None
    item.status = "running"
    item.eta = {"step_seconds": 10, "step_elapsed_seconds": 4, "remaining_seconds": 6}
    assert item.to_json()["seconds_left"] == 6
    assert item.to_json()["progress"] == 0.4
    item.finalizing = True
    assert item.to_json()["progress"] == 0.98
    item.status = "done"
    assert item.to_json()["seconds_left"] == 0


def test_length_failure_does_not_publish_or_start_remaining_garments(monkeypatch):
    item = avatar.TryOn("job", "owner", (), [("bottom", "b", "jeans"), ("top", "t", "shirt")], durable=True)
    monkeypatch.setattr(avatar.auth, "fetch_body_photo", AsyncMock(return_value=(b"person", "image/jpeg")))
    monkeypatch.setattr("frontend.connect.request_bytes", AsyncMock(return_value=(b"garment", "image/png")))
    submit = AsyncMock(
        return_value={"job_id": "gpu", "status": "failed", "error_code": "GARMENT_LENGTH_MISMATCH"}
    )
    monkeypatch.setattr(avatar.vton, "submit", submit)
    monkeypatch.setattr(avatar.vton, "delete_job", AsyncMock())
    calls = AsyncMock()
    monkeypatch.setattr(avatar, "api_request", calls)
    asyncio.run(avatar._run(item, "token"))
    assert item.status == "error" and "바지의 길이" in item.error
    assert submit.await_count == 1
    assert all(c.args[0] != "PUT" for c in calls.call_args_list)
    assert any(c.args[1].endswith("/cancel") for c in calls.call_args_list)


def test_legacy_saved_images_remain_history_but_are_not_reused_for_new_render():
    profile = SimpleNamespace(
        user_id="u",
        body_photo_updated_at="v1",
        saved_tryons=[{"id": "old", "status": "done", "items": {"top": "t"}, "photo_version": "v1"}],
    )
    assert avatar.tryon_state(request("token"), {"top": "t"}, profile) is None
    profile.saved_tryons[0]["render_version"] = "occlusion-length-v5"
    assert avatar.tryon_state(request("token"), {"top": "t"}, profile) is None
    assert profile.saved_tryons[0]["id"] == "old"
    profile.saved_tryons[0]["render_version"] = avatar.RENDER_VERSION
    assert avatar.tryon_state(request("token"), {"top": "t"}, profile)["id"] == "old"


def test_changed_clothes_bypass_both_saved_and_in_memory_cache(monkeypatch):
    key = avatar._key("u", {"top": "t"}, "v1")
    cached = avatar.TryOn("old", "u", key, [("top", "t", "top")], durable=True, status="done")
    monkeypatch.setattr(avatar, "_TRYONS", {"old": cached})
    profile = SimpleNamespace(
        user_id="u",
        body_photo_updated_at="v1",
        saved_tryons=[
            {
                "id": "old",
                "status": "done",
                "items": {"top": "t"},
                "photo_version": "v1",
                "render_version": avatar.RENDER_VERSION,
                "inputs_current": False,
            }
        ],
    )
    assert avatar.tryon_state(request("token"), {"top": "t"}, profile) is None
    assert "old" in avatar._TRYONS  # History and its image are not erased.
    profile.saved_tryons[0]["inputs_current"] = True
    assert avatar.tryon_state(request("token"), {"top": "t"}, profile)["id"] == "old"


@pytest.mark.parametrize("missing_top", [False, True])
def test_outer_fallback_is_explained_and_preservation_requires_completed_top(monkeypatch, missing_top):
    item = avatar.TryOn("job", "u", (), [("top", "t", "knit"), ("outerwear", "o", "jacket")], durable=True)
    monkeypatch.setattr(avatar.auth, "fetch_body_photo", AsyncMock(return_value=(b"person", "image/jpeg")))
    monkeypatch.setattr(
        "frontend.connect.request_bytes",
        AsyncMock(
            side_effect=[
                ApiError(404, "missing") if missing_top else (b"top", "image/png"),
                (b"outer", "image/png"),
            ]
        ),
    )
    submit = AsyncMock(return_value={"job_id": "gpu", "status": "done", "layering": {"status": "skipped"}})
    monkeypatch.setattr(avatar.vton, "submit", submit)
    monkeypatch.setattr(avatar.vton, "get_result", AsyncMock(return_value=b"result"))
    monkeypatch.setattr(avatar.vton, "delete_job", AsyncMock())
    publish = AsyncMock()
    monkeypatch.setattr(avatar, "api_request", publish)
    asyncio.run(avatar._run(item, "token"))
    assert item.status == "done"
    assert submit.call_args.kwargs["preserve_inner"] is (not missing_top)
    assert bool(item.to_json()["warnings"]) is (not missing_top)
    assert publish.call_args.kwargs["data"]["render_version"] == avatar.RENDER_VERSION


def test_durable_failure_releases_admission_and_passes_trusted_owner(monkeypatch):
    item = avatar.TryOn("job", "user-id", (), [("top", "top", "top")], durable=True)
    monkeypatch.setattr(avatar.auth, "fetch_body_photo", AsyncMock(return_value=(b"person", "image/jpeg")))
    monkeypatch.setattr("frontend.connect.request_bytes", AsyncMock(return_value=(b"garment", "image/png")))
    submit = AsyncMock(side_effect=ApiError(500, "model failed"))
    calls = AsyncMock(return_value={})
    monkeypatch.setattr(avatar.vton, "submit", submit)
    monkeypatch.setattr(avatar, "api_request", calls)
    asyncio.run(avatar._run(item, "token"))
    assert item.status == "error"
    assert submit.call_args.kwargs["owner_id"] == "user-id"
    assert submit.call_args.kwargs["request_id"]
    assert ("POST", "/tryon-results/job/cancel") in [c.args for c in calls.call_args_list]


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


def test_saved_gallery_renders_images_and_customer_retention_notice(monkeypatch):
    from frontend.app import app
    from frontend.connect.auth_profile import Profile

    monkeypatch.setattr(
        avatar.auth,
        "get_profile",
        AsyncMock(
            return_value=Profile(
                photo_policy="session",
                saved_tryons=[{"id": "saved-id", "image": "/tryon/saved-id/image", "names": ["니트"]}],
            )
        ),
    )
    with TestClient(app) as client:
        client.cookies.set("fitjang_token", "token")
        response = client.get("/tryon/history")
    assert response.status_code == 200
    assert "/tryon/saved-id/image" in response.text
    assert "/tryon/history/saved-id/delete" in response.text
    assert "30분" in response.text


def test_backend_managed_generation_releases_gpu_image_and_publishes_to_backend(monkeypatch):
    item = avatar.TryOn("job", "owner", (), [("top", "g", "top")], durable=True)
    monkeypatch.setattr(avatar.auth, "fetch_body_photo", AsyncMock(return_value=(b"person", "image/jpeg")))
    monkeypatch.setattr("frontend.connect.request_bytes", AsyncMock(return_value=(b"garment", "image/png")))
    monkeypatch.setattr(avatar.vton, "submit", AsyncMock(return_value={"job_id": "gpu", "status": "done"}))
    monkeypatch.setattr(avatar.vton, "get_result", AsyncMock(return_value=b"generated"))
    delete = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(avatar.vton, "delete_job", delete)
    monkeypatch.setattr(avatar, "api_request", publish)
    asyncio.run(avatar._run(item, "token"))
    assert item.status == "done" and item.image is None
    delete.assert_awaited_once_with("gpu")
    assert publish.call_args.args == ("PUT", "/tryon-results/job/image")
    assert publish.call_args.kwargs["files"]["file"][1] == b"generated"
