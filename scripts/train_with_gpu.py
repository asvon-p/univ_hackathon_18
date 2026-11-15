"""Pause try-on, run a Python training script, reap its workers, then restore try-on."""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
import uuid
from pathlib import Path

from scripts.frontend_preview import ROOT, deny_dotenv
from scripts.training_process import TrainingProcess
from scripts.tryon_control import Controller, control_lock

ACTIVE_PHASES = {"pausing", "running", "stopping", "restoring"}


def announce(message):
    try:
        print(message, flush=True)
    except OSError:
        pass


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(25):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 24:
                    raise
                time.sleep(0.04)
    finally:
        temporary.unlink(missing_ok=True)


def read_training(root: Path):
    path = root / ".demo/tryon-runtime/training.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"phase": "idle"}


def stop_training(root: Path):
    state = read_training(root)
    run_id = state.get("run_id", "")
    if state.get("phase") not in ACTIVE_PHASES or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise RuntimeError("No active training run to stop")
    # A stale report after a forced wrapper exit must not be mistaken for a live run.
    try:
        with control_lock(root):
            pass
    except RuntimeError as error:
        if "in progress" not in str(error):
            raise
    else:
        raise RuntimeError("Training wrapper is no longer running; inspect GPU status before resuming")
    (root / ".demo/training-runs" / run_id / "stop").touch()
    announce("Training stop requested. Waiting for cleanup and GPU restoration.")


class LogTail:
    def __init__(self, path):
        self.path, self.position = path, 0
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def emit(self, final=False):
        if self.path.exists():
            with self.path.open("rb") as stream:
                stream.seek(self.position)
                data = stream.read(65536)
                self.position = stream.tell()
            output = self.decoder.decode(data, final=final and not data)
            if output:
                try:
                    print(output, end="", flush=True)
                except (BrokenPipeError, OSError):
                    pass  # A closed output pipe must not prevent GPU cleanup.


def run_training(*, root, python, script, arguments, cwd, grace=15, timeout=600,
                 cancelled=None, controller=None, process_factory=TrainingProcess):
    controller = controller or Controller(root, timeout)
    cancelled = cancelled or threading.Event()
    with control_lock(root):
        run_id = uuid.uuid4().hex
        run_dir = root / ".demo/training-runs" / run_id
        run_dir.mkdir(parents=True)
        stop_file = run_dir / "stop"
        record = {"run_id": run_id, "phase": "pausing", "wrapper_pid": os.getpid(),
                  "training_pid": None, "training_exit_code": None, "exit_code": None}

        def publish(phase):
            record["phase"] = phase
            try:
                write_json(run_dir / "result.json", record)
                write_json(root / ".demo/tryon-runtime/training.json", record)
            except OSError:
                announce("Training status could not be saved; cleanup and GPU restoration will continue.")

        def stopping():
            return cancelled.is_set() or stop_file.exists()

        publish("pausing")
        process = None
        cleanup_ok = True
        exit_code = 2
        log = LogTail(run_dir / "training.log")
        try:
            announce("Waiting for try-on jobs, then releasing the GPU...")
            controller.pause(cancelled=stopping)
            if stopping():
                exit_code = 130
            else:
                process = process_factory()
                env = {**os.environ, "FITJANG_SKIP_DOTENV": "1", "PYTHONUNBUFFERED": "1",
                       "PYTHONIOENCODING": "utf-8", "FITJANG_GPU_TRAINING_RUN_ID": run_id,
                       "FITJANG_GPU_TRAINING_ROOT": str(root.resolve())}
                # The training project needs no demo credentials or demo storage settings.
                # Strip the obsolete invitation secret from inherited environments too.
                for name in ("TRYON_API_KEY", "JWT_SECRET", "DATABASE_URL", "TEST_DATABASE_URL",
                             "SIGNUP_INVITE_CODE", "TRYON_API_URL", "STORAGE_DIR", "PYTHONPATH"):
                    env.pop(name, None)
                command = [str(python), "-S", str(ROOT / "scripts/training_bootstrap.py"),
                           str(stop_file), str(script), *arguments]
                process.start(command, cwd=cwd, env=env, log=log.path)
                record["training_pid"] = process.pid
                publish("running")
                announce(f"Training started (PID {process.pid}). Ctrl+C or --stop requests cleanup.")
                deadline = None
                while process.poll() is None:
                    log.emit()
                    if stopping():
                        if deadline is None:
                            stop_file.touch()
                            deadline = time.monotonic() + grace
                            publish("stopping")
                        if time.monotonic() >= deadline:
                            break
                    time.sleep(0.1)
                result = process.poll()
                record["training_exit_code"] = result
                exit_code = 130 if stopping() or result == 0xC000013A else (
                    result if result is not None else 2
                )
        except BaseException as error:
            exit_code = 130 if stopping() or isinstance(error, KeyboardInterrupt) else 2
            record["error_type"] = type(error).__name__
            announce(f"Training wrapper stopped ({type(error).__name__}); restoring try-on after cleanup.")
        finally:
            # Always reap the whole training job before a single GPU model is loaded again.
            if process is not None:
                try:
                    process.finish()
                    if record["training_exit_code"] is None:
                        record["training_exit_code"] = process.poll()
                except BaseException as error:
                    cleanup_ok = False
                    record["cleanup_error_type"] = type(error).__name__
                finally:
                    process.close()
            try:
                log.emit(final=True)
            except OSError:
                pass
            if cleanup_ok:
                try:
                    publish("restoring")
                    announce("Training processes exited. Restoring try-on...")
                    status = controller.resume()
                    if not status.get("gpu_ready"):
                        raise RuntimeError("GPU readiness was not confirmed")
                except BaseException as error:
                    record["restore_error_type"] = type(error).__name__
                    exit_code = 70
                    phase = "restore_failed"
                    announce("GPU restoration failed. Check worker.log; retry scripts.tryon_control resume.")
                else:
                    phase = "finished"
                    announce("Try-on is ready again.")
            else:
                exit_code, phase = 71, "cleanup_failed"
                announce("Training cleanup could not be confirmed; try-on remains paused.")
            record["exit_code"] = exit_code
            publish(phase)
        return exit_code


def main():
    os.environ["FITJANG_SKIP_DOTENV"] = "1"
    sys.addaudithook(deny_dotenv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--stop", action="store_true")
    action.add_argument("--status", action="store_true")
    parser.add_argument("--python", default=sys.executable, help="Training environment's python.exe")
    parser.add_argument("--script", type=Path)
    parser.add_argument("--workdir", type=Path, help="Default: training script's directory")
    parser.add_argument("--grace", type=float, default=15, help="Training interruption cleanup timeout")
    parser.add_argument("--timeout", type=float, default=600, help="GPU switch timeout")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = args.project_root.resolve()
    if args.stop:
        stop_training(root)
        return 0
    if args.status:
        print(json.dumps(read_training(root), ensure_ascii=False))
        return 0
    executable = shutil.which(args.python)
    if not executable or not args.script or not args.script.is_file() or args.script.suffix.lower() != ".py":
        parser.error("--python must be an existing interpreter and --script must be an existing .py file")
    script = args.script.resolve()
    cwd = (args.workdir or script.parent).resolve()
    if not cwd.is_dir() or args.grace < 0 or args.timeout <= 0:
        parser.error("Invalid workdir, grace, or timeout")
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    cancelled = threading.Event()
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
        previous[sig] = signal.signal(sig, lambda *_: cancelled.set())
    try:
        return run_training(root=root, python=Path(executable), script=script,
                            arguments=arguments, cwd=cwd, grace=args.grace, timeout=args.timeout,
                            cancelled=cancelled)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else f"Training launch failed ({type(exc).__name__})",
              file=sys.stderr)
        raise SystemExit(2) from None
