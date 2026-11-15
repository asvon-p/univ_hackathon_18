"""Persistent mentor demo, without dotenv: python -m scripts.mentor_demo [--stop|--status].

Only the frontend is exposed, through Cloudflare or an existing HTTPS endpoint.
By default local and public demo servers share their persistent DB and images.
Stopping never drops a PostgreSQL schema or deletes uploaded images.
"""

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

from scripts.frontend_preview import deny_dotenv

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".demo" / "mentor-demo"
SCHEMA = "mentor_demo_v1"
DATABASE = "postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang"
FRONTEND_PORT = 3001
BACKEND_PORT = 8003


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def credentials():
    path = STATE / "credentials.json"
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "email": "mentor-demo@example.com",
                    "password": secrets.token_urlsafe(15),
                    "jwt_secret": secrets.token_urlsafe(48),
                },
                stream,
                indent=2,
            )
    return json.loads(path.read_text(encoding="utf-8"))


def seed(client, account):
    """Seed via the real API once; subsequent starts preserve all user edits/deletions."""
    from scripts.demo_assets import CATALOG, garment_images

    marker = STATE / "seed.json"
    if marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("complete"):
        return
    login = client.post("/api/v1/auth/login", json={k: account[k] for k in ("email", "password")})
    if login.status_code == 401:
        signup = client.post(
            "/api/v1/auth/signup",
            json={
                "email": account["email"],
                "password": account["password"],
                "nickname": "멘토 시연",
            },
        )
        signup.raise_for_status()
        token = signup.json()["access_token"]
    else:
        login.raise_for_status()
        token = login.json()["access_token"]
    client.headers["Authorization"] = "Bearer " + token
    state = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {"garments": {}}
    for item in CATALOG:
        if item.category == "shoes":
            continue
        garment_id = state["garments"].get(item.key)
        if garment_id is None:
            response = client.post(
                "/api/v1/garments",
                files={"file": (item.key + ".jpg", garment_images(item)[0], "image/jpeg")},
            )
            response.raise_for_status()
            garment_id = response.json()["garment_id"]
            state["garments"][item.key] = garment_id
            write_json(marker, state)
        response = client.patch(
            f"/api/v1/garments/{garment_id}",
            json={
                "name": "시연 / " + item.key,
                "category": item.category,
                "subcategory": item.subcategory,
                "color": item.color,
                "pattern": "solid",
                "thermal_level": item.thermal,
                "style_candidates": [item.style],
                "status": item.status,
            },
        )
        response.raise_for_status()
    state["complete"] = True
    write_json(marker, state)
    client.headers.pop("Authorization", None)


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--local-only", action="store_true", help="Validate before publishing")
    parser.add_argument("--public-url", help="Existing external HTTPS endpoint, used with --local-only")
    parser.add_argument("--tryon", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--recommender", choices=["auto", "local", "openai"], default="auto")
    parser.add_argument("--reuse-tunnel", action="store_true", help="Keep the saved live HTTPS tunnel")
    parser.add_argument(
        "--shared-local",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the persistent local DB and image store (default)",
    )
    args = parser.parse_args()
    if args.local_only and args.reuse_tunnel:
        parser.error("--local-only and --reuse-tunnel cannot be combined")
    if args.public_url and (
        not args.local_only or not re.fullmatch(r"https://[a-z0-9.-]+(?::[0-9]{1,5})?", args.public_url)
    ):
        parser.error("--public-url must be an HTTPS origin and requires --local-only")
    STATE.mkdir(parents=True, exist_ok=True)
    runtime = STATE / "runtime.json"
    if args.stop:
        (STATE / "stop").touch()
        print("Shutdown requested. Demo database and images will be retained.")
        return
    if args.status:
        print(runtime.read_text(encoding="utf-8") if runtime.exists() else "Demo is stopped.")
        return
    # Hold a manager lock for the full process lifetime, including startup/shutdown.
    with socket.socket() as lock:
        lock.bind(("127.0.0.1", 8004))
        run(args, runtime)


def run(args, runtime):
    for port in (FRONTEND_PORT, BACKEND_PORT):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    cloudflared = ROOT / ".demo" / "tools" / "cloudflared.exe"
    if not args.local_only and not cloudflared.is_file():
        raise RuntimeError("Install official cloudflared to .demo/tools/cloudflared.exe first")
    saved_tunnel = None
    public_url = args.public_url
    if args.reuse_tunnel:
        from scripts.demo_process import ExistingTunnel

        previous = json.loads(runtime.read_text(encoding="utf-8"))
        public_url = previous.get("public_url")
        if not isinstance(public_url, str) or not re.fullmatch(
            r"https://[a-z0-9-]+\.trycloudflare\.com", public_url
        ):
            raise RuntimeError("The saved HTTPS tunnel address is invalid")
        saved_tunnel = ExistingTunnel(previous.get("tunnel_pid", previous["pids"][-1]), cloudflared)
    account = credentials()
    if args.shared_local:
        from scripts.frontend_preview import persistent_jwt_secret
        from scripts.shared_demo_data import LOCAL_DATABASE, LOCAL_SCHEMA

        database, schema = LOCAL_DATABASE, LOCAL_SCHEMA
        storage_dir = ROOT / ".demo/frontend-preview/persistent/storage"
        jwt_secret = persistent_jwt_secret()
    else:
        database, schema = DATABASE, SCHEMA
        storage_dir, jwt_secret = STATE / "storage", account["jwt_secret"]
    import httpx
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from backend.app.db.engine import create_database_engine as create_engine

    url = make_url(database).update_query_dict({"options": f"-csearch_path={schema}"})
    from scripts.recommendation_runtime import frontend_environment, recommendation_environment

    judge_env = recommendation_environment(args.recommender)
    env = {
        **os.environ,
        "FITJANG_SKIP_DOTENV": "1",
        "APP_ENV": "demo",
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "JWT_SECRET": jwt_secret,
        "FITJANG_PUBLIC_ORIGIN": args.public_url or "",
        "NEW_USER_PHOTO_POLICY": "persistent",
        "STORAGE_DIR": str(storage_dir),
        "VISION_PROVIDER": "local",
        "WEATHER_PROVIDER": "open_meteo",
        "WEATHER_TIMEOUT_SEC": "5",
        "WEATHER_API_KEY": "",
        "SERVE_FRONTEND": "false",
        "STORAGE_DELETE_RETRY_SECONDS": "30",
        "FRONTEND_USE_MOCK": "false",
        "FRONTEND_BODY_PHOTO_ENABLED": "true",
        "BACKEND_API_URL": f"http://127.0.0.1:{BACKEND_PORT}/api/v1",
        "TRYON_API_URL": "",
        "TRYON_API_KEY": "",
        "LOG_LEVEL": "WARNING",
        "PYTHONIOENCODING": "utf-8",
        **judge_env,
    }
    print("Recommendation provider: " + judge_env["RECOMMENDER_PROVIDER"], flush=True)
    # Configure the migration process as well as child servers before imports.
    os.environ.update(frontend_environment(env))
    engine = create_engine(database, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config(str(ROOT / "backend/alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()
    processes, logs = [], []
    completed_startup = False
    tryon_managed = False
    (STATE / "stop").unlink(missing_ok=True)

    def spawn(name, arguments, process_env=None):
        log = (STATE / f"{name}.log").open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            arguments,
            cwd=ROOT,
            env=process_env if process_env is not None else frontend_environment(env),
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        processes.append(process)
        return process

    try:
        if args.tryon:
            from scripts.local_tryon import runtime_environment
            from scripts.tryon_control import ensure_running

            _, gpu_env = runtime_environment()
            env.update(
                {
                    key: gpu_env[key]
                    for key in (
                        "TRYON_API_URL",
                        "TRYON_API_KEY",
                        "TRYON_MAINTENANCE_FILE",
                    )
                }
            )
            state = ensure_running()
            print("GPU service: " + state["phase"], flush=True)
        for name, app, port, health in (
            ("backend", "backend.app.main:app", BACKEND_PORT, "/health"),
            ("frontend", "scripts.mentor_frontend:app", FRONTEND_PORT, "/login"),
        ):
            process = spawn(
                name,
                [sys.executable, "-m", "scripts.preview_server", app, str(port)],
                env if name == "backend" else frontend_environment(env),
            )
            with httpx.Client(trust_env=False, timeout=3) as client:
                deadline = time.monotonic() + (180 if name == "backend" else 60)
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"{name} exited; see .demo/mentor-demo/{name}.log")
                    try:
                        response = client.get(f"http://127.0.0.1:{port}{health}")
                        if response.status_code == 200:
                            if name == "backend" and response.json()["db"] != "ok":
                                raise RuntimeError("Demo database is unavailable")
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.3)
                else:
                    raise RuntimeError(f"{name} readiness timeout")
        if not args.shared_local:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{BACKEND_PORT}", timeout=40, trust_env=False
            ) as client:
                seed(client, account)
        tunnel = saved_tunnel
        if not args.local_only and saved_tunnel is None:
            config_path = STATE / "tunnel.yml"
            config_path.write_text("{}\n", encoding="utf-8")
            tunnel = spawn(
                "tunnel",
                [
                    str(cloudflared),
                    "tunnel",
                    "--config",
                    str(config_path),
                    "--no-autoupdate",
                    "--protocol",
                    "http2",
                    "--url",
                    f"http://127.0.0.1:{FRONTEND_PORT}",
                ],
            )
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if tunnel.poll() is not None:
                    raise RuntimeError("Tunnel exited; see .demo/mentor-demo/tunnel.log")
                output = (STATE / "tunnel.log").read_text(encoding="utf-8", errors="replace")
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", output)
                if match:
                    public_url = match.group(0)
                    break
                time.sleep(0.5)
            if public_url is None:
                raise RuntimeError("Temporary HTTPS URL was not issued")
        if saved_tunnel is not None:
            processes.append(saved_tunnel)
        state = {
            "public_url": public_url,
            "frontend_url": f"http://127.0.0.1:{FRONTEND_PORT}",
            "backend_url": f"http://127.0.0.1:{BACKEND_PORT}",
            "schema": schema,
            "shared_local": args.shared_local,
            "recommender": judge_env["RECOMMENDER_PROVIDER"],
            "judgment_model": judge_env["OPENAI_MODEL"],
            "storage_dir": str(storage_dir),
            "email": "test@example.com" if args.shared_local else account["email"],
            "credentials_file": str(STATE / "credentials.json"),
            "pids": [process.pid for process in processes],
            "manager_pid": os.getpid(),
            "tunnel_pid": tunnel.pid if tunnel is not None else None,
            "tryon": args.tryon,
            "tryon_managed": tryon_managed,
        }
        write_json(runtime, state)
        completed_startup = True
        print(json.dumps(state), flush=True)
        while not (STATE / "stop").exists():
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("A demo process exited unexpectedly")
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process is saved_tunnel and not completed_startup:
                continue
            if process.poll() is None:
                if sys.platform == "win32" and process is not saved_tunnel:
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
        if saved_tunnel is not None:
            saved_tunnel.close()
        if completed_startup:
            runtime.unlink(missing_ok=True)
        print("Demo stopped. Accounts, garments, recommendations and feedback are retained.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not log exception parameters that might contain SQL or credentials.
        print(f"Mentor demo failed ({type(exc).__name__}). See local demo logs.", flush=True)
        raise SystemExit(1) from None
