"""GPU switch regressions without importing torch, dotenv, or touching live processes."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from frontend import avatar_preview as avatar
from frontend.connect import ApiError
from scripts import tryon_control as control
from shared.tryon_maintenance import read_state, write_state


@pytest.fixture
def switch(tmp_path, monkeypatch):
    ctl = control.Controller(tmp_path, timeout=2)
    monkeypatch.setenv("TRYON_MAINTENANCE_FILE", str(ctl.state))
    monkeypatch.setattr(control, "runtime_environment", lambda root: (
        root / "python.exe", {"TRYON_API_KEY": "test-only"},
    ))
    monkeypatch.setattr(control, "workers", Mock(side_effect=[[{"pid": 123}], []]))
    monkeypatch.setattr(control, "stop_verified", Mock())
    monkeypatch.setattr(ctl, "frontend_states", Mock(return_value=[]))
    monkeypatch.setattr(ctl, "get", Mock(return_value=None))
    return ctl


def test_pause_stops_only_verified_gpu_and_is_idempotent(switch, monkeypatch):
    assert switch.pause()["phase"] == "paused"
    control.stop_verified.assert_called_once_with({"pid": 123})
    monkeypatch.setattr(control, "workers", lambda root: [])
    assert switch.pause()["phase"] == "paused"
    assert control.stop_verified.call_count == 1


def test_busy_frontend_cancels_pause_without_killing(switch):
    switch.timeout = 0.01
    switch.frontend_states.return_value = [{"active": 1}]
    with pytest.raises(RuntimeError, match="still active"):
        switch.pause()
    assert read_state(switch.state)["phase"] == "ready"
    control.stop_verified.assert_not_called()


def test_training_interrupt_cancels_drain_without_killing_gpu(switch):
    with pytest.raises(InterruptedError):
        switch.pause(cancelled=lambda: True)
    assert read_state(switch.state)["phase"] == "ready"
    control.stop_verified.assert_not_called()


def test_gpu_jobs_are_drained(switch):
    switch.get.side_effect = [[{"status": "running"}], [], None, None]
    switch.timeout = 2
    assert switch.pause()["phase"] == "paused"
    control.stop_verified.assert_called_once()


def test_stop_failure_keeps_requests_blocked(switch):
    control.stop_verified.side_effect = RuntimeError("stop failed")
    with pytest.raises(RuntimeError, match="stop failed"):
        switch.pause()
    assert read_state(switch.state)["phase"] == "error"


def test_unverified_port_is_not_stopped(switch, monkeypatch):
    monkeypatch.setattr(control, "workers", lambda root: [])
    switch.get.return_value = []
    with pytest.raises(RuntimeError, match="not owned"):
        switch.pause()
    control.stop_verified.assert_not_called()


def test_restarting_web_does_not_resume_training_gpu(switch, monkeypatch):
    write_state(switch.state, "paused")
    spawn = Mock()
    monkeypatch.setattr(control.subprocess, "Popen", spawn)
    assert switch.resume(automatic=True)["phase"] == "paused"
    spawn.assert_not_called()


def test_resume_waits_for_ready_and_authentication(switch, monkeypatch):
    monkeypatch.setattr(control, "workers", lambda root: [{"pid": 123}])
    switch.get.side_effect = [None, {"status": "ok"}, [], {"status": "ok"}]
    assert switch.resume()["phase"] == "ready"
    assert switch.get.call_args_list[2].args == ("/jobs", "test-only")


def test_model_load_failure_stays_unavailable(switch, monkeypatch):
    monkeypatch.setattr(control, "workers", lambda root: [{"pid": 123}])
    switch.get.side_effect = [None, {"status": "error"}]
    with pytest.raises(RuntimeError, match="failed to load"):
        switch.resume()
    assert not read_state(switch.state)["available"]


def test_legacy_manager_cannot_cascade_stop_web(tmp_path):
    folder = tmp_path / ".demo/frontend-preview"
    folder.mkdir(parents=True)
    (folder / "runtime.json").write_text(json.dumps({"tryon_managed": True, "pids": [123]}))
    with pytest.raises(RuntimeError, match="Legacy web manager"):
        control.Controller(tmp_path).frontend_states([{"pid": 123}])


def test_corrupt_state_fails_closed(switch):
    switch.state.parent.mkdir(parents=True)
    switch.state.write_text("invalid json")
    assert read_state(switch.state)["phase"] == "error"


def test_atomic_state_retries_transient_windows_sharing_error(switch, monkeypatch):
    from shared import tryon_maintenance as maintenance

    original = maintenance.os.replace
    calls = []

    def transient(source, destination):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("File temporarily held by a reader")
        original(source, destination)

    monkeypatch.setattr(maintenance.os, "replace", transient)
    write_state(switch.state, "paused")
    assert read_state(switch.state)["phase"] == "paused"
    assert len(calls) == 2


def test_paused_ui_refuses_before_profile_fetch(switch, monkeypatch):
    from backend.tests.test_tryon_frontend import request

    write_state(switch.state, "paused")
    monkeypatch.setattr(avatar.vton, "enabled", lambda: True)
    profile = AsyncMock()
    monkeypatch.setattr(avatar.auth, "get_profile", profile)
    response = asyncio.run(avatar.tryon_start(request("token"), "top", "bottom", ""))
    assert response.status_code == 503
    assert json.loads(response.body)["code"] == "TRYON_PAUSED"
    assert response.headers["retry-after"] == "30"
    profile.assert_not_called()


def test_pause_during_await_does_not_enqueue_a_new_job(switch, monkeypatch):
    from backend.tests.test_tryon_frontend import request

    monkeypatch.setattr(avatar.vton, "enabled", lambda: True)
    monkeypatch.setattr(avatar, "_TRYONS", {})

    async def profile(token):
        write_state(switch.state, "draining")
        return SimpleNamespace(body_photo_updated_at="version", user_id="user")

    monkeypatch.setattr(avatar.auth, "get_profile", profile)
    garment = SimpleNamespace(garment_id="top")
    monkeypatch.setattr(avatar.closet, "get_garment", AsyncMock(return_value=garment))
    monkeypatch.setattr(avatar.closet, "may_have_photo", lambda g: True)
    monkeypatch.setattr(avatar, "garment_name", lambda g: "top")
    response = asyncio.run(avatar.tryon_start(request("token"), "top", "", ""))
    assert response.status_code == 503
    assert not avatar._TRYONS


def test_connector_blocks_gpu_post_during_pause(switch, monkeypatch):
    write_state(switch.state, "paused")
    monkeypatch.setattr(avatar.vton, "enabled", lambda: True)
    with pytest.raises(ApiError) as error:
        asyncio.run(avatar.vton.submit((b"p", "image/jpeg"), (b"g", "image/jpeg"), "tops"))
    assert error.value.code == "TRYON_PAUSED"


def test_os_lock_prevents_concurrent_switches(tmp_path):
    with control.control_lock(tmp_path), pytest.raises(RuntimeError, match="in progress"):
        with control.control_lock(tmp_path):
            pytest.fail("Second switch acquired the same lock")
