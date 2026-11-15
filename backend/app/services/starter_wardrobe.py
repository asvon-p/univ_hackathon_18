"""Prepared synthetic garments, copied into each new owner's private storage."""

import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from backend.app.db.models import Garment, GarmentAttribute
from backend.app.services.thumbnails import thumbnail_key

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "starter_wardrobe"


@lru_cache(maxsize=1)
def catalog():
    manifest = json.loads((ASSETS / "catalog.json").read_text(encoding="utf-8"))
    entries = manifest["garments"]
    if Counter(item["category"] for item in entries) != {"top": 3, "bottom": 3, "outerwear": 3}:
        raise ValueError("Invalid starter wardrobe category counts")
    prepared = []
    for item in entries:
        files = []
        for field, digest in [("image", "sha256"), ("thumbnail", "thumbnail_sha256")]:
            path = (ASSETS / item[field]).resolve()
            if path.parent != ASSETS.resolve():
                raise ValueError("Invalid starter image path")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != item[digest]:
                raise ValueError("Starter image checksum mismatch")
            files.append(data)
        prepared.append((item, *files))
    return manifest["version"], prepared


def create_starter_wardrobe(db, storage, user_id):
    """Called once inside the signup transaction; never on login or restart."""
    version, entries = catalog()
    for item, original, thumbnail in entries:
        gid = uuid4()
        key = f"{user_id}/{gid}/original.jpg"
        garment = Garment(
            id=gid,
            user_id=user_id,
            name=item["name"],
            is_example=True,
            original_path=key,
            status="available",
            analysis_status="done",
            attributes=GarmentAttribute(
                **{
                    k: item[k]
                    for k in (
                        "category",
                        "subcategory",
                        "color",
                        "pattern",
                        "thermal_level",
                        "style_candidates",
                    )
                },
                source="model",
                model_version=version,
            ),
        )
        storage.save(key, original)
        storage.save(thumbnail_key(garment, key), thumbnail)
        db.add(garment)
