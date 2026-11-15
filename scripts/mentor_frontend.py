"""Public demo boundary: application pages only, never a workspace file server."""

import os
import secrets
import time
from collections import deque
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse

from frontend.app import app as frontend_app
from shared.memory_uploads import MemoryUploadMiddleware

app = FastAPI(title="Fitjang Public Demo", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(MemoryUploadMiddleware)
app.include_router(frontend_app.router)

PRIVATE_ROOTS = {
    "backend",
    "frontend",
    "scripts",
    "shared",
    "recommendation",
    "vision",
    "tryon_server",
    "storage",
    "uploads",
    "docs",
    "redoc",
    "openapi.json",
    "api",
    "debug",
    "__debug__",
    "editor",
    "terminal",
    "vscode",
    "node_modules",
    "venv",
    "__pycache__",
}
SOURCE_SUFFIXES = (
    ".py",
    ".pyc",
    ".pyo",
    ".ps1",
    ".bat",
    ".sh",
    ".md",
    ".toml",
    ".ini",
    ".yaml",
    ".yml",
    ".sql",
    ".sqlite",
    ".db",
    ".map",
    ".bak",
    ".log",
    ".lock",
    ".txt",
    ".json",
    ".zip",
    ".tar",
    ".gz",
)


def private_path(path: str) -> bool:
    # No filesystem probing: even blocked requests must never open .env or source files.
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    if any(value in path for value in ("%", "\\", "\x00", ":")):
        return True
    parts = [part.casefold() for part in path.split("/") if part]
    return bool(parts) and (
        any(part.startswith(".") for part in parts)
        or parts[0] in PRIVATE_ROOTS
        or parts[-1].endswith(SOURCE_SUFFIXES)
    )


class AuthRequestLimit:
    """Bounded, single-process public login/signup burst limiter; no credentials recorded."""

    def __init__(self):
        self.buckets = {}

    def allow(self, path, client, now=None):
        now = time.monotonic() if now is None else now
        limits = (20, 100) if path == "/signup" else (60, 300)
        self.buckets = {key: value for key, value in self.buckets.items() if value and value[-1] > now - 60}
        keys = [(path, client), (path, "global")]
        if len(self.buckets) >= 2048 and keys[0] not in self.buckets:
            return False
        buckets = [self.buckets.setdefault(key, deque()) for key in keys]
        for bucket in buckets:
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
        if any(len(bucket) >= limit for bucket, limit in zip(buckets, limits, strict=True)):
            return False
        for bucket in buckets:
            bucket.append(now)
        return True


limiter = AuthRequestLimit()


def same_origin(value, request):
    try:
        source = urlsplit(value)
        return (source.scheme, source.netloc) == (request.url.scheme, request.headers.get("host"))
    except ValueError:
        return False


@app.middleware("http")
async def demo_headers(request, call_next):
    nonce = secrets.token_urlsafe(24)
    request.state.csp_nonce = nonce
    mutating = request.method not in ("GET", "HEAD", "OPTIONS")
    source = request.headers.get("origin") or request.headers.get("referer")
    if private_path(request.url.path):
        response = PlainTextResponse("Not Found", status_code=404)
    elif request.method not in ("GET", "HEAD", "POST", "OPTIONS"):
        response = PlainTextResponse("Method Not Allowed", status_code=405)
    elif mutating and (
        request.headers.get("sec-fetch-site") in ("cross-site", "same-site")
        or (source and not same_origin(source, request))
        or (request.url.scheme == "https" and not source)
    ):
        response = PlainTextResponse("Cross-origin form submission is not allowed", status_code=403)
    elif (
        request.method == "POST"
        and request.url.path in ("/signup", "/login")
        and not limiter.allow(request.url.path, request.client.host if request.client else "unknown")
    ):
        response = PlainTextResponse(
            "Too many attempts. Try again in one minute.", status_code=429, headers={"Retry-After": "60"}
        )
    else:
        response = await call_next(request)
    camera_policy = response.headers.get("Permissions-Policy", "camera=()")
    response.headers.update(
        {
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "same-origin",
            "Permissions-Policy": f"{camera_policy}, microphone=(), geolocation=(self), usb=()",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; script-src-attr 'none'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; "
                "font-src 'self'; base-uri 'none'; form-action 'self'; "
                "frame-ancestors 'none'; object-src 'none'"
            ),
        }
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=86400"
        response.raw_headers = [
            (key, value + b"; Secure" if key == b"set-cookie" and b"secure" not in value.lower() else value)
            for key, value in response.raw_headers
        ]
    return response


public_host = urlsplit(os.environ.get("FITJANG_PUBLIC_ORIGIN", "")).hostname
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", public_host or "*.trycloudflare.com"]
)
