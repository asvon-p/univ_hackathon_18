import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.demo_autostart import Supervisor, safe_environment, validate_funnel


@pytest.fixture
def supervisor(tmp_path):
    return Supervisor(
        {
            "python": "python.exe",
            "docker": "docker.exe",
            "tailscale": "tailscale.exe",
            "public_url": "https://demo.example.ts.net:8443",
            "volume": "existing-pgdata",
            "container": "existing-db",
        },
        tmp_path,
    )


def result(code=0, content=None):
    return SimpleNamespace(returncode=code, stdout=json.dumps(content or {}).encode())


def test_database_boot_uses_existing_volume_and_explicit_env_file(supervisor):
    supervisor.docker = Mock(side_effect=[result(), result(), result(1), result()])
    supervisor.ensure_database()
    args = supervisor.docker.call_args_list[-1].args
    assert "--env-file" in args and str(args[args.index("--env-file") + 1]).endswith(".env.example")
    assert "--no-recreate" in args and "--wait" in args
    assert args[-1] == "db"
    assert safe_environment()["FITJANG_SKIP_DOTENV"] == safe_environment()["COMPOSE_DISABLE_ENV_FILE"] == "1"


def test_missing_original_volume_never_initializes_empty_database(supervisor):
    supervisor.docker = Mock(side_effect=[result(), RuntimeError("Missing volume")])
    with pytest.raises(RuntimeError, match="Missing volume"):
        supervisor.ensure_database()
    assert all(call.args[0] != "compose" for call in supervisor.docker.call_args_list)


def test_docker_not_ready_starts_desktop_and_defers_database(supervisor):
    supervisor.docker = Mock(return_value=result(1))
    supervisor.command = Mock(return_value=result())
    with pytest.raises(RuntimeError, match="Waiting for Docker"):
        supervisor.ensure_database()
    assert supervisor.command.call_args.args[0] == ["docker.exe", "desktop", "start", "--detach"]
    assert supervisor.docker.call_count == 1


@pytest.mark.parametrize(
    "config",
    [
        {"Web": {"demo.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}}},
        {"TCP": {"8443": {"TCPForward": "127.0.0.1:9999"}}},
    ],
)
def test_funnel_does_not_overwrite_other_services(config):
    with pytest.raises(RuntimeError, match="another"):
        validate_funnel(config, "demo.example.ts.net:8443")


def test_existing_funnel_and_private_443_are_accepted_without_mutation():
    config = {
        "TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
        "Web": {
            "demo.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}},
            "demo.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3001"}}},
        },
        "AllowFunnel": {"demo.example.ts.net:8443": True},
    }
    original = json.dumps(config)
    assert validate_funnel(config, "demo.example.ts.net:8443")
    assert json.dumps(config) == original


def test_gpu_training_pause_is_preserved(supervisor, monkeypatch):
    from scripts import tryon_control
    from shared import tryon_maintenance

    ctl = Mock()
    monkeypatch.setattr(tryon_control, "Controller", Mock(return_value=ctl))
    monkeypatch.setattr(tryon_maintenance, "read_state", lambda _: {"phase": "paused"})
    assert supervisor.ensure_gpu() == "paused"
    ctl.resume.assert_not_called()
    ctl.status.assert_not_called()


def test_occupied_mentor_port_is_never_killed_or_replaced(supervisor, monkeypatch):
    import httpx

    from scripts import demo_autostart

    client = Mock()
    client.get.side_effect = httpx.ConnectError("not ready")
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(demo_autostart.httpx, "Client", Mock(return_value=client))
    monkeypatch.setattr(demo_autostart, "free_ports", lambda _: False)
    spawn = Mock()
    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="no processes were killed"):
        supervisor.ensure_mentor()
    spawn.assert_not_called()


def test_tick_does_not_launch_anything_after_database_failure(supervisor):
    supervisor.validate_existing_data = Mock()
    supervisor.ensure_database = Mock(side_effect=RuntimeError("DB unavailable"))
    supervisor.ensure_gpu = Mock()
    supervisor.ensure_mentor = Mock()
    with pytest.raises(RuntimeError, match="DB unavailable"):
        supervisor.tick()
    supervisor.ensure_gpu.assert_not_called()
    supervisor.ensure_mentor.assert_not_called()


def test_stop_during_gpu_warmup_does_not_start_mentor(supervisor):
    supervisor.validate_existing_data = Mock()
    supervisor.ensure_database = Mock()
    supervisor.ensure_funnel = Mock()
    supervisor.ensure_gpu = Mock(side_effect=lambda: (supervisor.state / "stop").touch())
    supervisor.ensure_mentor = Mock()
    assert supervisor.tick() == "stopping"
    supervisor.ensure_mentor.assert_not_called()


def test_missing_credentials_do_not_regenerate_keys(supervisor, monkeypatch, tmp_path):
    from scripts import demo_autostart

    monkeypatch.setattr(demo_autostart, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="credential file is missing"):
        supervisor.validate_existing_data()
    assert not (tmp_path / ".demo").exists()
