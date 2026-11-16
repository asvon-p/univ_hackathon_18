"""HTTP UI + real backend/PG, replacing only GPU inference with synthetic output."""

import json
import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw

if os.environ.get("TEST_TRYON_STUB") != "1" or os.environ.get("APP_ENV") != "test":
    raise RuntimeError("This frontend is for isolated tests only")

if os.environ.get("TEST_TRYON_MENTOR") == "1":
    from scripts.mentor_frontend import app as app
else:
    from frontend.app import app as app

from frontend.connect import tryon


async def submit(person, garment, category, **kwargs):
    with Path(os.environ["TEST_TRYON_CALLS"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"category": category, "owner": kwargs.get("owner_id")}) + "\n")
    return {"job_id": str(uuid4()), "status": "done", "layering": {"status": "applied"}}


async def get_result(_):
    # Clearly synthetic test mannequin; no real person's photo is used or retained.
    image = Image.new("RGB", (288, 432), "#eeeeee")
    draw = ImageDraw.Draw(image)
    draw.ellipse((118, 20, 170, 72), fill="#b8b8b8")
    draw.rectangle((94, 80, 194, 230), fill="#405570")
    draw.rectangle((94, 230, 134, 400), fill="#1a2838")
    draw.rectangle((154, 230, 194, 400), fill="#1a2838")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def delete_job(_):
    return None


tryon.submit, tryon.get_result, tryon.delete_job = submit, get_result, delete_job
