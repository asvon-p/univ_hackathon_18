"""Bounded multipart parsing without SpooledTemporaryFile rollover to disk."""

from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartParser

MAX_REQUEST_BYTES = 45 * 1024 * 1024  # GPU accepts two images of up to 20 MiB each.


class MemoryUploadMiddleware:
    def __init__(self, app):
        self.app = app
        MultiPartParser.spool_max_size = MAX_REQUEST_BYTES + 1
        MultiPartParser.max_file_size = MAX_REQUEST_BYTES + 1  # older Starlette versions

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > MAX_REQUEST_BYTES:
                raise HTTPException(413, "Upload exceeds request size limit")
            return message

        await self.app(scope, bounded_receive, send)
