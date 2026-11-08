from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image

from backend.app.db.models import Garment


@pytest.fixture
def add_garment(api_client, db_session):
    image = BytesIO()
    Image.new("RGB", (32, 32), "navy").save(image, format="PNG")

    def add(account, category, *, status="available", analysis_status="done", styles=None):
        response = api_client.post(
            "/api/v1/garments",
            headers=account["headers"],
            files={"file": ("garment.png", image.getvalue(), "image/png")},
        )
        assert response.status_code == 201, response.text
        garment_id = response.json()["garment_id"]
        response = api_client.patch(
            f"/api/v1/garments/{garment_id}",
            headers=account["headers"],
            json={"category": category, "status": status, "style_candidates": styles or ["minimal"]},
        )
        assert response.status_code == 200, response.text
        if analysis_status != "done":
            db_session.get(Garment, UUID(garment_id)).analysis_status = analysis_status
            db_session.commit()
        return garment_id

    return add


@pytest.fixture
def closet(auth_account, add_garment):
    return {c: add_garment(auth_account, c) for c in ("top", "bottom", "shoes", "outerwear")}
