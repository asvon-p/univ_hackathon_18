"""Logon supervisor for the existing demo. Never initializes accounts or reads dotenv."""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx

from scripts.frontend_preview import ROOT, deny_dotenv

STATE = ROOT / ".demo/autostart"
LOG = logging.getLogger("demo_autostart")


def safe_environment():
    return {
        **os.environ,
        "FITJANG_SKIP_DOTENV": "1",
        "COMPOSE_DISABLE_ENV_FILE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }


@contextmanager
def singleton(path: Path):
    import msvcrt

    with path.open("a+b") as stream:
        if not path.stat().st_size:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("Autostart supervisor is already running") from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def free_ports(ports):
    for port in ports:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                return False
    return True


def validate_funnel(config, endpoint):
    """Never replace another service, nor expose the existing private 443 listener."""
    web = config.get("Web", {})
    matching = {key: value for key, value in web.items() if key.endswith(":8443")}
    expected = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3001"}}}
    if matching and matching != {endpoint: expected}:
        raise RuntimeError("Funnel 8443 belongs to another service; left unchanged")
    if "8443" in config.get("TCP", {}) and not matching:
        raise RuntimeError("TCP 8443 already has another configuration; left unchanged")
    return matching and config.get("AllowFunnel", {}).get(endpoint) is True


class Supervisor:
    def __init__(self, config, state=STATE):
        self.config = config
        self.state = state
        self.mentor = None

    def validate_existing_data(self):
        # Never let a missing state directory silently generate replacement signing/API keys.
        for name in (
            "frontend-preview/credentials.json",
            "mentor-demo/credentials.json",
            "tryon-runtime/credentials.json",
        ):
            if not (ROOT / ".demo" / name).is_file():
                raise RuntimeError("An existing credential file is missing; automatic startup refused")
        if not (ROOT / ".demo/frontend-preview/persistent/storage").is_dir():
            raise RuntimeError("Existing image storage is missing; automatic startup refused")

    def command(self, arguments, *, timeout=30, required=True):
        result = subprocess.run(
            [str(arg) for arg in arguments],
            cwd=ROOT,
            env=safe_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if required and result.returncode:
            # Raw command output can contain private paths or provider diagnostics.
            raise RuntimeError(f"{Path(arguments[0]).name} command failed ({result.returncode})")
        return result

    def docker(self, *arguments, **options):
        return self.command([self.config["docker"], "--context", "desktop-linux", *arguments], **options)

    def ensure_database(self):
        if self.docker("info", "--format", "{{.ServerVersion}}", required=False).returncode:
            self.command([self.config["docker"], "desktop", "start", "--detach"], timeout=45)
            raise RuntimeError("Waiting for Docker Desktop")
        # Refuse to create an empty substitute DB if the original volume is unavailable.
        self.docker("volume", "inspect", self.config["volume"])
        current = self.docker(
            "inspect", self.config["container"], "--format", "{{json .State}}", required=False
        )
        if not current.returncode:
            status = json.loads(current.stdout)
            if status.get("Running") and status.get("Health", {}).get("Status") == "healthy":
                return
        self.docker(
            "compose",
            "--env-file",
            ROOT / ".env.example",
            "--project-directory",
            ROOT,
            "--project-name",
            "univ_hackathon_18",
            "-f",
            ROOT / "docker-compose.yml",
            "up",
            "-d",
            "--no-recreate",
            "--wait",
            "--wait-timeout",
            "120",
            "db",
            timeout=150,
        )

    def ensure_funnel(self):
        exe = self.config["tailscale"]
        status = json.loads(self.command([exe, "status", "--json"]).stdout)
        if status.get("BackendState") != "Running":
            raise RuntimeError("Waiting for the existing Tailscale login/network")
        host = status.get("Self", {}).get("DNSName", "").rstrip(".")
        if "https://" + host + ":8443" != self.config["public_url"]:
            raise RuntimeError("Tailscale hostname changed; fixed address was not replaced")
        config = json.loads(self.command([exe, "funnel", "status", "--json"]).stdout)
        if not validate_funnel(config, host + ":8443"):
            self.command([exe, "funnel", "--bg", "--https=8443", "--yes", "http://127.0.0.1:3001"])

    def ensure_gpu(self):
        from scripts.tryon_control import Controller, control_lock
        from shared.tryon_maintenance import read_state

        ctl = Controller(ROOT)
        phase = read_state(ctl.state)["phase"]
        if phase == "error":
            try:
                stored = json.loads(ctl.state.read_text(encoding="utf-8"))
                if stored.get("phase") != "error":
                    raise ValueError("Invalid phase")
            except (OSError, ValueError, AttributeError):
                raise RuntimeError("GPU maintenance state is corrupt; inspect it before resuming") from None
        if phase in ("paused", "draining"):
            return "paused"  # Training has priority, including across a supervisor restart.
        status = ctl.status()
        if status["gpu_ready"] and phase == "ready":
            return "ready"
        with control_lock(ROOT):
            # Recheck after locking: a training pause may have won the race.
            if read_state(ctl.state)["phase"] in ("paused", "draining"):
                return "paused"
            ctl.resume()
        return "ready"

    def ensure_mentor(self):
        with httpx.Client(trust_env=False, timeout=3) as client:
            try:
                health = client.get("http://127.0.0.1:8003/health")
                ui = client.get("http://127.0.0.1:3001/login")
                if health.status_code == ui.status_code == 200 and health.json().get("db") == "ok":
                    runtime = json.loads(
                        (ROOT / ".demo/mentor-demo/runtime.json").read_text(encoding="utf-8")
                    )
                    if (
                        not runtime.get("shared_local")
                        or runtime.get("public_url") != self.config["public_url"]
                    ):
                        raise RuntimeError("Existing mentor runtime differs; left unchanged")
                    return True
            except (httpx.HTTPError, FileNotFoundError):
                pass
        if self.mentor is not None and self.mentor.poll() is None:
            return False  # The manager may still be applying migrations or warming up.
        if not free_ports((3001, 8003, 8004)):
            raise RuntimeError("Waiting for existing mentor processes; no processes were killed")
        with (self.state / "mentor-launch.log").open("a", encoding="utf-8") as log:
            self.mentor = subprocess.Popen(
                [
                    self.config["python"],
                    "-m",
                    "scripts.mentor_demo",
                    "--local-only",
                    "--shared-local",
                    "--tryon",
                    "--public-url",
                    self.config["public_url"],
                ],
                cwd=ROOT,
                env=safe_environment(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        return False

    def tick(self):
        self.validate_existing_data()
        self.ensure_database()
        self.ensure_funnel()
        if (self.state / "stop").exists():
            return "stopping"
        gpu = self.ensure_gpu()
        if (self.state / "stop").exists():
            return "stopping"
        if not self.ensure_mentor():
            return "starting_mentor"
        return "ready" if gpu == "ready" else "ready_gpu_paused"


def main():
    os.environ.update(safe_environment())
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true", help="Stop supervision; keep servers/data")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    status_path, stop = STATE / "status.json", STATE / "stop"
    if args.stop:
        stop.touch()
        return
    if args.status:
        print(status_path.read_text(encoding="utf-8") if status_path.exists() else "Not started")
        return
    handler = RotatingFileHandler(
        STATE / "supervisor.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    with singleton(STATE / "supervisor.lock"):
        stop.unlink(missing_ok=True)
        supervisor = Supervisor(json.loads((STATE / "config.json").read_text(encoding="utf-8")))
        last = None
        LOG.info("Supervisor started; pid=%s; no dotenv; existing data only", os.getpid())
        while not stop.exists():
            try:
                phase = supervisor.tick()
                error = None
            except Exception as exc:
                phase = "retrying"
                error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            if (phase, error) != last:
                LOG.info("%s%s", phase, ": " + error if error else "")
                last = phase, error
            record = {
                "pid": os.getpid(),
                "phase": phase,
                "error": error,
                "updated_at": datetime.now(UTC).isoformat(),
                "public_url": supervisor.config["public_url"],
            }
            temporary = status_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
            temporary.replace(status_path)
            for _ in range(30):
                if stop.exists():
                    break
                time.sleep(1)
        record["phase"] = "stopped"
        status_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        LOG.info("Supervision stopped; running servers and data retained")


if __name__ == "__main__":
    main()
