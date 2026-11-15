"""Windows GPU-only switch: pause / resume / status. Never opens dotenv or the DB."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from scripts.frontend_preview import ROOT, deny_dotenv
from scripts.local_tryon import PORT, runtime_environment
from shared.tryon_maintenance import read_state, write_state


def powershell(script: str):
    if os.name != "nt":
        raise RuntimeError("This process controller supports Windows only")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Windows process inspection/control failed; no other services were stopped")
    return json.loads(result.stdout.decode("utf-8-sig")) if result.stdout.strip() else None


def workers(root: Path) -> list[dict]:
    """Match the runtime's venv launcher, then only its exact-module child interpreter."""
    rows = powershell(r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
@(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'scripts\.local_tryon' } |
  ForEach-Object { [pscustomobject]@{
    pid=$_.ProcessId; parent=$_.ParentProcessId; exe=$_.ExecutablePath; command=$_.CommandLine;
    created=$_.CreationDate.ToUniversalTime().Ticks.ToString()
  } }) | ConvertTo-Json -Compress
""") or []
    if isinstance(rows, dict):
        rows = [rows]
    expected = root / ".demo/tryon-runtime/venv/Scripts/python.exe"
    pattern = r'\s*(?:"[^"]+"|\S+)\s+-m\s+scripts\.local_tryon\s*'
    rows = [r for r in rows if re.fullmatch(pattern, r["command"])]
    parents = [r for r in rows if r.get("exe") and Path(r["exe"]).resolve() == expected.resolve()]
    if len(parents) > 1:
        raise RuntimeError("Multiple GPU launchers found; refusing ambiguous process control")
    parent_ids = {r["pid"] for r in parents}
    children = [r for r in rows if r["parent"] in parent_ids and r["pid"] not in parent_ids]
    return children + parents


def stop_verified(process: dict) -> None:
    """Hold a process handle and compare its creation time to avoid killing a reused PID."""
    pid, created = int(process["pid"]), int(process["created"])
    powershell(f"""
$ErrorActionPreference='Stop'
$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue
if ($null -ne $p) {{
  try {{
    $handle = $p.Handle
    $ticks = $p.StartTime.ToUniversalTime().Ticks
    if (($ticks - ($ticks % 10)) -ne {created}) {{ throw 'PID was reused' }}
    $p.Kill()
    if (-not $p.WaitForExit(15000)) {{ throw 'GPU process did not exit' }}
  }} catch {{
    if (-not $p.HasExited) {{ throw }}
  }}
}}
exit 0
""")


@contextmanager
def control_lock(root: Path):
    """OS-released lock; a crashed CLI cannot leave a permanent stale lock."""
    path = root / ".demo/tryon-runtime/control.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("Another GPU switch is in progress") from None
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            raise RuntimeError("This process controller supports Windows only")


class Controller:
    def __init__(self, root: Path = ROOT, timeout: float = 600):
        self.root = root.resolve()
        self.state = self.root / ".demo/tryon-runtime/maintenance.json"
        self.timeout = timeout
        self.url = f"http://127.0.0.1:{PORT}"

    def get(self, path: str, key: str | None = None):
        with httpx.Client(trust_env=False, timeout=3) as client:
            try:
                response = client.get(self.url + path, headers={"x-api-key": key} if key else {})
            except httpx.ConnectError:
                return None
            response.raise_for_status()
            return response.json()

    def status(self):
        health = self.get("/health")
        return {**read_state(self.state), "gpu_online": health is not None,
                "gpu_ready": bool(health and health.get("status") == "ok")}

    def frontend_states(self, processes: list[dict]):
        """Read only non-secret runtime metadata; never stop a web manager or its children."""
        ids = {p["pid"] for p in processes}
        statuses = []
        for name in ("frontend-preview", "mentor-demo"):
            path = self.root / ".demo" / name / "runtime.json"
            if not path.exists():
                continue
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("tryon_managed") and ids.intersection(state.get("pids", [])):
                raise RuntimeError(
                    "Legacy web manager owns GPU. Update/restart that launcher before switching"
                )
            # Only the project's two loopback frontends can influence local process control.
            url = state.get("frontend_url", "")
            if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", url):
                raise RuntimeError("Invalid local frontend runtime URL")
            with httpx.Client(trust_env=False, timeout=3) as client:
                try:
                    response = client.get(url + "/tryon/service-status")
                except httpx.ConnectError:
                    continue
                if response.status_code != 200:
                    raise RuntimeError("Running frontend needs the GPU-switch update before pausing")
                status = response.json()
                if status.get("phase") != "draining":
                    raise RuntimeError("Frontend uses a different maintenance file; GPU was not stopped")
                statuses.append(status)
        return statuses

    def pause(self, *, cancelled=None):
        previous = read_state(self.state)["phase"]
        processes = workers(self.root)
        if previous == "paused" and not processes and self.get("/health") is None:
            return self.status()
        _, env = runtime_environment(self.root)
        write_state(self.state, "draining")
        stopping = False
        try:
            deadline = time.monotonic() + self.timeout
            while True:
                if cancelled is not None and cancelled():
                    raise InterruptedError("GPU pause was cancelled before training")
                fronts = self.frontend_states(processes)
                jobs = self.get("/jobs", env["TRYON_API_KEY"])
                if jobs is not None and not processes:
                    raise RuntimeError("Port 8010 is not owned by the verified GPU runtime")
                active = any(s.get("active", 0) for s in fronts) or any(
                    j["status"] in ("queued", "running") for j in (jobs or [])
                )
                if not active:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Try-on jobs are still active; pause cancelled without stopping GPU")
                time.sleep(0.5)
            write_state(self.state, "paused")
            stopping = True
            for process in processes:
                stop_verified(process)
            if workers(self.root) or self.get("/health") is not None:
                raise RuntimeError("GPU shutdown could not be confirmed; do not start training yet")
        except BaseException:
            write_state(self.state, "error" if stopping else previous)
            raise
        return self.status()

    def resume(self, *, automatic: bool = False):
        if automatic and read_state(self.state)["phase"] != "ready":
            # Starting/restarting a website must not steal the GPU from training.
            return self.status()
        python, env = runtime_environment(self.root)
        processes = workers(self.root)
        health = self.get("/health")
        if health is not None and not processes:
            raise RuntimeError("Port 8010 belongs to an unverified process; refusing to reuse it")
        write_state(self.state, "starting")
        try:
            launched = None
            if not processes:
                log_path = self.root / ".demo/tryon-runtime/worker.log"
                with log_path.open("a", encoding="utf-8") as log:
                    launched = subprocess.Popen(
                        [str(python), "-m", "scripts.local_tryon"], cwd=ROOT,
                        env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"},
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if launched is not None and launched.poll() is not None:
                    raise RuntimeError("GPU process exited; inspect .demo/tryon-runtime/worker.log")
                health = self.get("/health")
                if health and health.get("status") == "error":
                    raise RuntimeError("GPU model failed to load; inspect .demo/tryon-runtime/worker.log")
                if health and health.get("status") == "ok":
                    self.get("/jobs", env["TRYON_API_KEY"])  # Verify the existing key before reopening.
                    write_state(self.state, "ready")
                    return self.status()
                time.sleep(0.5)
            raise RuntimeError("GPU startup timed out; maintenance remains enabled")
        except BaseException:
            write_state(self.state, "error")
            raise


def ensure_running(root: Path = ROOT):
    controller = Controller(root)
    if read_state(controller.state)["phase"] != "ready":
        # A training wrapper holds the control lock for its entire run. Web startup still works.
        return controller.status()
    with control_lock(root):
        return controller.resume(automatic=True)


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "resume", "status"))
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    controller = Controller(args.project_root, args.timeout)
    if args.action == "status":
        result = controller.status()
    else:
        with control_lock(controller.root):
            result = getattr(controller, args.action)()
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, httpx.HTTPError) as exc:
        # HTTP exceptions may contain URLs; never dump process environment or credentials.
        print(str(exc) if isinstance(exc, RuntimeError) else f"GPU switch failed ({type(exc).__name__})",
              file=sys.stderr)
        raise SystemExit(1) from None
