"""실제 TCP HTTP 서버 + 전용 PostgreSQL 스키마. 서비스/인증/어댑터 대체 없음."""

import os
import re
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from backend.app.core.config import ROOT_DIR, get_settings
from backend.app.db.engine import create_database_engine as create_engine


@pytest.fixture
def live_api(request, tmp_path):
    options = request.param if isinstance(request.param, dict) else {"provider": request.param}
    provider = options["provider"]
    if os.environ.get("TEST_POSTGRES") != "1":
        pytest.skip("실제 HTTP E2E는 TEST_POSTGRES=1 필요")
    url = make_url(get_settings().TEST_DATABASE_URL)
    schema = f"test_e2e_{uuid4().hex}"
    assert re.fullmatch(r"test_e2e_[0-9a-f]{32}", schema)
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    process = None
    replica = None
    log_path = tmp_path / "server.log"
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config(str(ROOT_DIR / "backend/alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with socket.socket() as socket_:
            socket_.bind(("127.0.0.1", 0))
            port = socket_.getsockname()[1]
        child_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        env = {
            **os.environ,
            "APP_ENV": "test",
            "STARTER_WARDROBE_ENABLED": "true" if options.get("starter_wardrobe") else "false",
            "FITJANG_SKIP_DOTENV": "1",
            "TEST_DATABASE_URL": child_url.render_as_string(hide_password=False),
            "STORAGE_DIR": str(tmp_path / "storage"),
            "VISION_PROVIDER": provider,
            "VISION_CLASSIFIER": options.get("classifier", "demo"),
            "VISION_SEGMENTATION": options.get("segmentation", "flat"),
            "VISION_DEVICE": "cpu",
            "VISION_TIMEOUT_SEC": str(options.get("vision_timeout", 20)),
            "RECOMMENDER_PROVIDER": provider,
            "WEATHER_PROVIDER": "open_meteo" if options.get("weather_stub") else "manual",
            "WEATHER_CACHE_TTL_SEC": str(options.get("weather_ttl", 300)),
            "TEST_WEATHER_SCENARIO": str(tmp_path / "weather-scenario.json"),
            "TEST_WEATHER_CALLS": str(tmp_path / "weather-calls.jsonl"),
            "SERVE_FRONTEND": "false",
            "NEW_USER_PHOTO_POLICY": options.get("photo_policy", "persistent"),
            "LOG_LEVEL": "WARNING",
            "STORAGE_DELETE_RETRY_SECONDS": "0.2",
            "API_USAGE_ENABLED": "true" if options.get('openai_stub') else "false",
        }
        if options.get("openai_stub"):
            env.update(
                RECOMMENDER_PROVIDER="openai",
                OPENAI_API_KEY="e2e-fake-key",
                TEST_OPENAI_FAILURE="1" if options["openai_stub"] == "failure" else "0",
                TEST_OPENAI_CALLS_PATH=str(tmp_path / "openai-calls"),
                TEST_OPENAI_DELAY=str(options.get("openai_delay", 0)),
            )
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "backend.tests.e2e.openai_server:app"
                    if options.get("openai_stub")
                    else "backend.tests.e2e.weather_server:app"
                    if options.get("weather_stub")
                    else "backend.app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                    "--log-level",
                    "warning",
                ],
                cwd=ROOT_DIR,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False) as client:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail("E2E server exited: " + log_path.read_text(encoding="utf-8"))
                    try:
                        response = client.get("/health", timeout=1)
                        if response.status_code == 200 and response.json()["db"] == "ok":
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("E2E server readiness timed out")
                runtime = {
                    "database_url": child_url,
                    "storage_dir": tmp_path / "storage",
                    "openai_calls": tmp_path / "openai-calls",
                }
                if options.get("replica"):
                    with socket.socket() as probe:
                        probe.bind(("127.0.0.1", 0))
                        replica_port = probe.getsockname()[1]
                    replica = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "backend.tests.e2e.openai_server:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(replica_port),
                            "--no-access-log",
                        ],
                        cwd=ROOT_DIR,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    runtime["replica_url"] = f"http://127.0.0.1:{replica_port}"
                    with httpx.Client(base_url=runtime["replica_url"], trust_env=False) as secondary:
                        deadline = time.monotonic() + 60
                        while time.monotonic() < deadline:
                            try:
                                if secondary.get("/health", timeout=1).status_code == 200:
                                    break
                            except httpx.RequestError:
                                pass
                            time.sleep(0.1)
                        else:
                            pytest.fail("Replica readiness timed out")
                yield client, provider, runtime
    finally:
        if replica is not None:
            replica.terminate()
            try:
                replica.wait(timeout=10)
            except subprocess.TimeoutExpired:
                replica.kill()
                replica.wait(timeout=5)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        # 공개 스키마/개발 데이터가 아닌 이 fixture가 생성한 스키마만 삭제합니다.
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def live_frontend(live_api, tmp_path, request):
    """Integrated D UI server; every form request calls the real backend over TCP."""
    backend, provider, runtime = live_api
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        **os.environ,
        "FITJANG_SKIP_DOTENV": "1",
        "FRONTEND_USE_MOCK": "false",
        "FRONTEND_BODY_PHOTO_ENABLED": "true" if getattr(request, "param", {}).get("body_photo") else "false",
        "BACKEND_API_URL": str(backend.base_url).rstrip("/") + "/api/v1",
        "TRYON_API_URL": "",
        "TRYON_API_KEY": "",
        "TRYON_MAINTENANCE_FILE": str(tmp_path / "tryon-maintenance.json"),
    }
    if getattr(request, "param", {}).get("tryon"):
        env.update(TRYON_API_URL="http://127.0.0.1:1", TRYON_API_KEY="test-only-gpu-key")
    if getattr(request, "param", {}).get("tryon_stub"):
        env.update(
            TEST_TRYON_STUB="1",
            TEST_TRYON_MENTOR="1" if getattr(request, "param", {}).get("mentor") else "0",
            TEST_TRYON_CALLS=str(tmp_path / "gpu-calls.jsonl"),
        )
        runtime["gpu_calls"] = tmp_path / "gpu-calls.jsonl"
    runtime["tryon_maintenance"] = tmp_path / "tryon-maintenance.json"
    with (tmp_path / "frontend.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "scripts.preview_server",
                "backend.tests.e2e.tryon_frontend:app"
                if getattr(request, "param", {}).get("tryon_stub")
                else "scripts.mentor_frontend:app"
                if getattr(request, "param", {}).get("mentor")
                else "frontend.app:app",
                str(port),
            ],
            cwd=ROOT_DIR,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=40, trust_env=False) as ui:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(
                            "Frontend exited: " + (tmp_path / "frontend.log").read_text(encoding="utf-8")
                        )
                    try:
                        if ui.get("/login").status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("Frontend readiness timeout")
                yield ui, backend, runtime
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
