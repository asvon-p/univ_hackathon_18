"""Windows training lifecycle: real child processes, isolated files, stub GPU controller."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import train_with_gpu as runner
from scripts.training_process import TrainingProcess
from scripts.tryon_control import Controller, control_lock, ensure_running
from shared.tryon_maintenance import write_state

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process tree controller")


class GPU:
    def __init__(self):
        self.events = []

    def pause(self, *, cancelled):
        self.events.append("pause")
        if cancelled():
            raise InterruptedError("cancelled")

    def resume(self):
        self.events.append("resume")
        return {"gpu_ready": True}


def execute(tmp_path, source, *, gpu=None, arguments=(), cancelled=None, grace=1, factory=TrainingProcess):
    folder = tmp_path / "학습 폴더"
    folder.mkdir(exist_ok=True)
    script = folder / "학습 코드.py"
    script.write_text(source, encoding="utf-8")
    return runner.run_training(root=tmp_path, python=Path(sys.executable), script=script,
                               arguments=list(arguments), cwd=folder, grace=grace, timeout=2,
                               controller=gpu or GPU(), cancelled=cancelled, process_factory=factory)


@pytest.mark.parametrize("code", [0, 7])
def test_real_training_exit_preserves_argv_cwd_and_resumes(tmp_path, code):
    gpu = GPU()
    source = """import json, os, sys
from pathlib import Path
Path('args.json').write_text(json.dumps({'args':sys.argv[1:],'skip':os.getenv('FITJANG_SKIP_DOTENV')}))
raise SystemExit(CODE)
""".replace("CODE", str(code))
    assert execute(tmp_path, source, gpu=gpu, arguments=["--name", "공백 있는 값"]) == code
    assert gpu.events == ["pause", "resume"]
    assert json.loads((tmp_path / "학습 폴더/args.json").read_text()) == {
        "args": ["--name", "공백 있는 값"], "skip": "1",
    }
    state = runner.read_training(tmp_path)
    assert state["phase"] == "finished" and state["training_exit_code"] == code


def test_exception_in_training_restores_gpu(tmp_path):
    gpu = GPU()
    assert execute(tmp_path, "raise RuntimeError('synthetic training error')", gpu=gpu) == 1
    assert gpu.events == ["pause", "resume"]


def test_training_child_receives_matching_gpu_session(tmp_path):
    source = """import json, os
from pathlib import Path
root = Path(os.environ['FITJANG_GPU_TRAINING_ROOT'])
state = json.loads((root / '.demo/tryon-runtime/training.json').read_text(encoding='utf-8'))
assert state['run_id'] == os.environ['FITJANG_GPU_TRAINING_RUN_ID']
assert state['phase'] in ('pausing', 'running')
"""
    assert execute(tmp_path, source) == 0


def test_spawn_worker_and_parent_cannot_open_dotenv(tmp_path):
    source = """import multiprocessing as mp
from pathlib import Path
def attempt():
    try:
        open('.env').read()
    except RuntimeError:
        return True
    return False
def worker(q):
    q.put(attempt())
if __name__ == '__main__':
    assert attempt()
    q=mp.Queue(); p=mp.Process(target=worker,args=(q,)); p.start()
    assert q.get(timeout=10) is True
    p.join(10); assert p.exitcode == 0
    assert not Path('.env').exists()
"""
    assert execute(tmp_path, source) == 0


@pytest.mark.parametrize("cooperative", [True, False])
def test_stop_waits_for_or_terminates_training_tree_before_resume(tmp_path, cooperative):
    gpu = GPU()
    cancelled = threading.Event()
    source = """import time
from pathlib import Path
Path('started').touch()
try:
    while True:
        try: time.sleep(.05)
        except KeyboardInterrupt:
            if COOPERATIVE: raise
finally:
    Path('cleaned').touch()
""".replace("COOPERATIVE", str(cooperative))

    def cancel():
        deadline = time.monotonic() + 10
        while not (tmp_path / "학습 폴더/started").exists():
            if time.monotonic() > deadline:
                return
            time.sleep(.05)
        cancelled.set()

    thread = threading.Thread(target=cancel)
    thread.start()
    assert execute(tmp_path, source, gpu=gpu, cancelled=cancelled, grace=.5) == 130
    thread.join(10)
    assert gpu.events == ["pause", "resume"]
    assert (tmp_path / "학습 폴더/cleaned").exists() == cooperative


def test_orphan_worker_is_reaped_and_unrelated_process_survives(tmp_path):
    source = """import subprocess, sys
subprocess.Popen([sys.executable,'-S','-c','import time;time.sleep(60)'])
"""
    seen = []

    class CheckedProcess(TrainingProcess):
        def finish(self, timeout=30):
            assert self.active_count() >= 1  # Main process exited, its child is still alive.
            super().finish(timeout)
            seen.append(self.active_count())

    unrelated = subprocess.Popen([sys.executable, "-S", "-c", "import time;time.sleep(60)"],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        gpu = GPU()
        original = gpu.resume

        def resume():
            assert seen == [0]
            assert unrelated.poll() is None
            return original()

        gpu.resume = resume
        assert execute(tmp_path, source, gpu=gpu, factory=CheckedProcess) == 0
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=10)


def test_failed_pause_never_starts_training_and_attempts_recovery(tmp_path):
    gpu = GPU()
    gpu.pause = Mock(side_effect=RuntimeError("cannot pause"))
    factory = Mock()
    assert execute(tmp_path, "pass", gpu=gpu, factory=factory) == 2
    factory.assert_not_called()
    assert gpu.events == ["resume"]


def test_failed_restore_is_not_reported_as_success(tmp_path):
    gpu = GPU()
    gpu.resume = Mock(side_effect=RuntimeError("model failed"))
    assert execute(tmp_path, "raise SystemExit(7)", gpu=gpu) == 70
    state = runner.read_training(tmp_path)
    assert state["phase"] == "restore_failed" and state["training_exit_code"] == 7


def test_cleanup_failure_blocks_gpu_restart(tmp_path):
    gpu = GPU()
    fake = Mock(pid=999)
    fake.poll.return_value = 0
    fake.finish.side_effect = RuntimeError("unconfirmed workers")
    assert execute(tmp_path, "pass", gpu=gpu, factory=lambda: fake) == 71
    assert gpu.events == ["pause"]
    assert runner.read_training(tmp_path)["phase"] == "cleanup_failed"


def test_report_write_error_does_not_skip_gpu_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "write_json", Mock(side_effect=PermissionError("held file")))
    gpu = GPU()
    assert execute(tmp_path, "pass", gpu=gpu) == 0
    assert gpu.events == ["pause", "resume"]


def test_live_stop_command_and_lock_isolation(tmp_path):
    gpu = GPU()
    source = "import time\nfrom pathlib import Path\nPath('started').touch()\nwhile True: time.sleep(.1)"
    errors = []

    def stop():
        try:
            deadline = time.monotonic() + 10
            while not (tmp_path / "학습 폴더/started").exists():
                if time.monotonic() > deadline:
                    raise TimeoutError()
                time.sleep(.05)
            with pytest.raises(RuntimeError, match="in progress"), control_lock(tmp_path):
                pass
            runner.stop_training(tmp_path)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=stop)
    thread.start()
    assert execute(tmp_path, source, gpu=gpu) == 130
    thread.join(10)
    assert not errors
    assert gpu.events == ["pause", "resume"]


def test_ctrl_c_handler_requests_cleanup_without_raising(tmp_path, monkeypatch):
    script = tmp_path / "train.py"
    script.write_text("pass")
    monkeypatch.setattr(sys, "argv", ["train_with_gpu", "--script", str(script)])

    def run(**kwargs):
        signal.raise_signal(signal.SIGINT)
        assert kwargs["cancelled"].is_set()
        return 130

    old = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(runner, "run_training", run)
    assert runner.main() == 130
    assert signal.getsignal(signal.SIGINT) == old


def test_web_can_start_during_training_lock(tmp_path, monkeypatch):
    state = tmp_path / ".demo/tryon-runtime/maintenance.json"
    write_state(state, "paused")
    monkeypatch.setattr(Controller, "status", lambda self: {"phase": "paused", "gpu_online": False})
    with control_lock(tmp_path):
        assert ensure_running(tmp_path)["phase"] == "paused"


def test_missing_script_fails_before_touching_gpu(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["train_with_gpu", "--script", str(tmp_path / "missing.py")])
    run = Mock()
    monkeypatch.setattr(runner, "run_training", run)
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    run.assert_not_called()
