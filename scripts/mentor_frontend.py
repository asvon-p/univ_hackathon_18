"""HTTPS demo wrapper; normal local frontend behavior is unchanged."""

from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse

from frontend.app import app


@app.middleware("http")
async def demo_headers(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        if urlsplit(origin).netloc != request.headers.get("host"):
            return PlainTextResponse("Cross-origin form submission is not allowed", status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if request.url.scheme == "https":
        response.raw_headers = [
            (key, value + b"; Secure" if key == b"set-cookie" and b"secure" not in value.lower() else value)
            for key, value in response.raw_headers
        ]
    return response
