"""Run the local UI with persistent PostgreSQL data without opening .env.

python -m scripts.frontend_preview
python -m scripts.frontend_preview --stop
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".demo" / "frontend-preview"
SCHEMA = "ui_preview_local"


def deny_dotenv(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.fsdecode(args[0]).replace("\\", "/").rsplit("/", 1)[-1].lower() == ".env":
            raise RuntimeError("This preview must not open .env")


def persistent_jwt_secret() -> str:
    """Reuse the local signing key so restarting does not invalidate existing sessions."""
    path = STATE / "credentials.json"
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"jwt_secret": secrets.token_urlsafe(48)}, stream)
    secret = json.loads(path.read_text(encoding="utf-8"))["jwt_secret"]
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise RuntimeError("Invalid local signing key; existing credentials were not overwritten")
    return secret


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--backend-port", type=int, default=8002)
    parser.add_argument("--weather", choices=["open_meteo", "manual"], default="open_meteo")
    parser.add_argument("--tryon", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    if args.stop:
        (STATE / "stop").touch()
        print("Local server shutdown requested. Accounts, data and images will be retained.")
        return
    for port in (args.frontend_port, args.backend_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    os.environ.update(
        FITJANG_SKIP_DOTENV="1",
        APP_ENV="test",
        JWT_SECRET=persistent_jwt_secret(),
        VISION_PROVIDER="local",
        RECOMMENDER_PROVIDER="local",
        WEATHER_PROVIDER=args.weather,
        WEATHER_TIMEOUT_SEC="5",
        WEATHER_CACHE_TTL_SEC="300",
        SIGNUP_INVITE_CODE="",
        OPENAI_API_KEY="",
        LLM_API_KEY="",
        WEATHER_API_KEY="",
        SERVE_FRONTEND="false",
        STORAGE_DELETE_RETRY_SECONDS="30",
        LOG_LEVEL="WARNING",
        PYTHONIOENCODING="utf-8",
        FRONTEND_USE_MOCK="false",
        FRONTEND_BODY_PHOTO_ENABLED="true",
        TRYON_API_URL="",
        TRYON_API_KEY="",
    )
    import httpx
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from scripts.demo_assets import CATALOG, garment_images

    # Public development fixture credentials only. Never resolve a URL from dotenv.
    url = make_url("postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test")
    schema = SCHEMA
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    run_dir = STATE / "persistent"
    run_dir.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "TEST_DATABASE_URL": url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
            hide_password=False
        ),
        "STORAGE_DIR": str(run_dir / "storage"),
        "BACKEND_API_URL": f"http://127.0.0.1:{args.backend_port}/api/v1",
    }
    for category in ("top", "bottom", "outerwear"):
        item = next(g for g in CATALOG if g.category == category)
        (run_dir / f"{category}.jpg").write_bytes(garment_images(item)[0])
    (STATE / "stop").unlink(missing_ok=True)
    processes, logs = [], []
    tryon_managed = False
    try:
        from scripts.local_tryon import CREDENTIALS, PORT, runtime_environment

        use_tryon = args.tryon if args.tryon is not None else CREDENTIALS.exists()
        if use_tryon:
            gpu_python, gpu_env = runtime_environment()
            env.update({key: gpu_env[key] for key in ("TRYON_API_URL", "TRYON_API_KEY")})
            with httpx.Client(trust_env=False) as client:
                gpu = None
                try:
                    response = client.get(f"http://127.0.0.1:{PORT}/health", timeout=2)
                    response.raise_for_status()
                except httpx.ConnectError:
                    with socket.socket() as probe:
                        probe.bind(("127.0.0.1", PORT))
                    log = (run_dir / "tryon.log").open("w", encoding="utf-8")
                    logs.append(log)
                    gpu = subprocess.Popen(
                        [str(gpu_python), "-m", "scripts.local_tryon"],
                        cwd=ROOT,
                        env={**env, **gpu_env},
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    processes.append(gpu)
                    tryon_managed = True
                    print("Loading local GPU model; see persistent/tryon.log", flush=True)
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    if gpu is not None and gpu.poll() is not None:
                        raise RuntimeError("GPU server exited; see persistent/tryon.log")
                    try:
                        health = client.get(f"http://127.0.0.1:{PORT}/health", timeout=2).json()
                        if health["status"] == "ok":
                            client.get(
                                f"http://127.0.0.1:{PORT}/jobs",
                                headers={"x-api-key": gpu_env["TRYON_API_KEY"]},
                            ).raise_for_status()
                            print("GPU ready: " + health["gpu"], flush=True)
                            break
                        if health["status"] == "error":
                            raise RuntimeError("GPU model failed to load; see persistent/tryon.log")
                    except httpx.RequestError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError("GPU model readiness timeout")
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config(str(ROOT / "backend/alembic.ini"))
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
        for name, app, port, health in (
            ("backend", "backend.app.main:app", args.backend_port, "/health"),
            ("frontend", "frontend.app:app", args.frontend_port, "/login"),
        ):
            log = (run_dir / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            process = subprocess.Popen(
                [sys.executable, "-m", "scripts.preview_server", app, str(port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            processes.append(process)
            with httpx.Client(trust_env=False) as client:
                deadline = time.monotonic() + (180 if name == "backend" else 30)
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"{name} exited; see its preview log")
                    try:
                        if client.get(f"http://127.0.0.1:{port}{health}", timeout=1).status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f"{name} readiness timeout")
        state = {
            "schema": schema,
            "persistent": True,
            "frontend_url": f"http://127.0.0.1:{args.frontend_port}",
            "backend_url": f"http://127.0.0.1:{args.backend_port}",
            "run_dir": str(run_dir),
            "weather": args.weather,
            "tryon": use_tryon,
            "tryon_managed": tryon_managed,
            "manager_pid": os.getpid(),
            "pids": [p.pid for p in processes],
        }
        (STATE / "runtime.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(json.dumps(state), flush=True)
        while not (STATE / "stop").exists():
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("Preview server exited unexpectedly")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if sys.platform == "win32":
                    # Windows venv python.exe may be a launcher with an interpreter child.
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        check=False,
                    )
                    process.wait(timeout=10)
                    continue
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        engine.dispose()
        (STATE / "runtime.json").unlink(missing_ok=True)
        print("Local server stopped. Accounts, garments, recommendations and images retained.", flush=True)


if __name__ == "__main__":
    main()
