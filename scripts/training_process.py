"""Windows process tree owned exclusively by one training run (no PID-wide kills)."""

import ctypes
import os
import subprocess
import time
from ctypes import wintypes as w


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
        ("flags", w.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_limit", w.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits), ("io_counters", ctypes.c_ulonglong * 6),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_longlong), ("kernel_time", ctypes.c_longlong),
        ("period_user_time", ctypes.c_longlong), ("period_kernel_time", ctypes.c_longlong),
        ("page_faults", w.DWORD), ("total_processes", w.DWORD),
        ("active_processes", w.DWORD), ("terminated_processes", w.DWORD),
    ]


class TrainingProcess:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("The training wrapper currently supports Windows only")
        self.kernel = k = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "QueryInformationJobObject": (
                [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL,
            ),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(k, name)
            function.argtypes, function.restype = args, result
        self.job = k.CreateJobObjectW(None, None)
        self.process = None
        self.pid = None
        self.returncode = None
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway permission.
        if not k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def start(self, command, *, cwd, env, log):
        import _winapi
        import msvcrt

        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESTDHANDLES
        with open(os.devnull, "rb") as stdin, open(log, "ab", buffering=0) as output:
            handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in (stdin, output)]
            startup.hStdInput, startup.hStdOutput, startup.hStdError = handles[0], handles[1], handles[1]
            startup.lpAttributeList = {"handle_list": handles}
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            try:
                # Suspended creation also covers venv redirectors: no child can escape before assignment.
                hp, ht, self.pid, _ = _winapi.CreateProcess(
                    command[0], subprocess.list2cmdline(command), None, None, True,
                    0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    env, str(cwd), startup,
                )
            finally:
                for handle in handles:
                    os.set_handle_inheritable(handle, False)
        self.process = hp
        try:
            if not self.kernel.AssignProcessToJobObject(self.job, hp):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.kernel.ResumeThread(ht) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.kernel.TerminateProcess(hp, 2)
            self.kernel.WaitForSingleObject(hp, 15000)
            raise
        finally:
            self.kernel.CloseHandle(ht)

    def poll(self):
        if self.process is None:
            return None
        if self.returncode is not None:
            return self.returncode
        wait = self.kernel.WaitForSingleObject(self.process, 0)
        if wait == 258:
            return None
        if wait != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = w.DWORD()
        if not self.kernel.GetExitCodeProcess(self.process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.returncode = code.value
        return self.returncode

    def active_count(self):
        info = Accounting()
        if not self.kernel.QueryInformationJobObject(
            self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.active_processes

    def finish(self, timeout=30):
        """Reap any leftover workers, including children of a training process that already exited."""
        if self.active_count() and not self.kernel.TerminateJobObject(self.job, 130):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + timeout
        while self.active_count():
            if time.monotonic() >= deadline:
                raise RuntimeError("Training workers are still alive; GPU resume is unsafe")
            time.sleep(0.1)
        if self.process is not None and self.kernel.WaitForSingleObject(self.process, 15000) != 0:
            raise RuntimeError("Training process exit could not be confirmed")

    def close(self):
        if self.process:
            self.kernel.CloseHandle(self.process)
            self.process = None
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
