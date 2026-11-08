"""Adopt an existing Windows tunnel without restarting its temporary HTTPS address."""

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path


class ExistingTunnel:
    def __init__(self, pid: int, executable: Path):
        if os.name != "nt":
            raise RuntimeError("Reusing a tunnel is currently supported on Windows only")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("Invalid tunnel PID")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        self.pid = pid
        self.handle = kernel.OpenProcess(0x100000 | 0x1000 | 0x0001, False, pid)
        if not self.handle:
            raise RuntimeError("The saved tunnel is no longer running")
        try:
            name, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if not kernel.QueryFullProcessImageNameW(self.handle, 0, name, ctypes.byref(size)):
                raise RuntimeError("Cannot verify saved tunnel executable")
            if Path(name.value).resolve() != executable.resolve() or self.poll() is not None:
                raise RuntimeError("Saved PID is not the expected live cloudflared process")
        except Exception:
            self.close()
            raise

    def poll(self):
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise RuntimeError("Cannot inspect adopted tunnel")
        return None if code.value == 259 else code.value

    def terminate(self):
        if self.poll() is None and not self.kernel.TerminateProcess(self.handle, 0):
            raise RuntimeError("Cannot stop adopted tunnel")

    kill = terminate

    def wait(self, timeout):
        result = self.kernel.WaitForSingleObject(self.handle, int(timeout * 1000))
        if result == 258:
            raise subprocess.TimeoutExpired("cloudflared", timeout)
        if result != 0:
            raise RuntimeError("Cannot wait for adopted tunnel")
        return self.poll()

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
