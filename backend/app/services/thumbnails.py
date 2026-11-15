"""Derived garment files only; never overwrite an original or a versioned cutout."""

import hashlib
from io import BytesIO
from pathlib import PurePosixPath

from PIL import Image, ImageOps

from backend.app.storage.base import InvalidStorageKey, StoragePort

THUMBNAIL_EDGE = 384
THUMBNAIL_VERSION = "v1"


def source_key(storage: StoragePort, garment) -> str:
    prefix = PurePosixPath(f"{garment.user_id}/{garment.id}")
    for key in (garment.cutout_path, garment.original_path):
        if key:
            if PurePosixPath(key).parent != prefix:
                raise InvalidStorageKey(key)
            if storage.exists(key):
                return key
    raise FileNotFoundError("Garment source image is missing")


def thumbnail_key(garment, source: str) -> str:
    # Originals are immutable; each successful reanalysis publishes a new cutout path.
    version = hashlib.sha256(source.encode()).hexdigest()[:24]
    return f"{garment.user_id}/{garment.id}/thumb-{THUMBNAIL_VERSION}-{THUMBNAIL_EDGE}-{version}.webp"


def create_thumbnail(storage: StoragePort, source: str) -> bytes:
    with storage.open(source) as stream, Image.open(stream) as image:
        if image.width * image.height > 64_000_000:
            raise ValueError("Garment image exceeds pixel limit")
        image = ImageOps.exif_transpose(image)
        image.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE), Image.Resampling.LANCZOS)
        clean = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        clean.info.clear()
        output = BytesIO()
        clean.save(output, format="WEBP", quality=82, method=4)
        return output.getvalue()
