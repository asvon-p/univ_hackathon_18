"""Run with python -S: install the dotenv guard before loading any environment packages."""

import os
import sys


def deny_dotenv(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.fsdecode(args[0]).replace("\\", "/").rsplit("/", 1)[-1].lower() == ".env":
            raise RuntimeError("Training must not read or write .env")


sys.addaudithook(deny_dotenv)
os.environ["FITJANG_SKIP_DOTENV"] = "1"


def guarded_spawn(**kwargs):
    """Keep the audit hook in Windows DataLoader/multiprocessing spawn children too."""
    from multiprocessing.spawn import get_executable

    return [get_executable(), "-S", os.path.abspath(__file__), "--multiprocessing-fork",
            *(f"{key}={value}" for key, value in kwargs.items())]


def main():
    import site

    site.main()  # .pth/sitecustomize imports run only after the audit hook is installed.
    from multiprocessing import spawn

    spawn.get_command_line = guarded_spawn
    if len(sys.argv) > 1 and sys.argv[1] == "--multiprocessing-fork":
        spawn.freeze_support()
        return

    import _thread
    import runpy
    import threading
    import time

    stop_path, script, *arguments = sys.argv[1:]

    def watch_stop():
        while not os.path.exists(stop_path):
            time.sleep(0.1)
        _thread.interrupt_main()  # Normal KeyboardInterrupt: training can run its finally/checkpoint code.

    threading.Thread(target=watch_stop, daemon=True, name="training-stop").start()
    sys.argv = [script, *arguments]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
