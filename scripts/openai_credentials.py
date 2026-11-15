"""Windows per-user encrypted API key, independent of dotenv and Git.

Run interactively: python -m scripts.openai_credentials --configure
The value is never printed, passed as an argument, or stored as plaintext.
"""

import argparse
import ctypes
import getpass
import os
from pathlib import Path

KEY_FILE = Path(__file__).resolve().parents[1] / ".demo/openai-judge/key.dpapi"


def _crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Encrypted demo credentials require Windows")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buffer), Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; no LOCAL_MACHINE flag: this Windows user only.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("Windows credential encryption/decryption failed")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def save_key(key: str, path: Path = KEY_FILE) -> None:
    key = key.strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Enter a nonempty API key without whitespace")
    encrypted = _crypt(key.encode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    temporary.replace(path)


def load_key(path: Path = KEY_FILE) -> str:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    if key:
        return key.strip()
    if not path.is_file():
        return ""
    return _crypt(path.read_bytes(), decrypt=True).decode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure", action="store_true")
    args = parser.parse_args()
    if args.configure:
        key = getpass.getpass("OpenAI API key (hidden; never paste into chat): ")
        save_key(key)
        print("API key encrypted for this Windows user. Restart the demo servers to apply.")
    else:
        print("API key configured: " + str(bool(load_key())))


if __name__ == "__main__":
    main()
