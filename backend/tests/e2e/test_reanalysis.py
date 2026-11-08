from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.adapters.vision import MockVision, SafeVision
from backend.app.db.models import Garment
from backend.app.services.garment import GarmentService
from backend.app.storage.local import LocalStorage
from scripts.demo_assets import CATALOG, garment_images


def register(client):
    credentials = {"email": f"retry-{uuid4().hex}@example.com", "password": "e2e-only-password-123"}
    signup = client.post(
        "/api/v1/auth/signup",
        json={**credentials, "nickname": "retry", "invite_code": "e2e-invite-only"},
    )
    assert signup.status_code == 201
    login = client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200
    return UUID(signup.json()["id"]), {"Authorization": "Bearer " + login.json()["access_token"]}


def upload(client, headers):
    result = client.post(
        "/api/v1/garments",
        headers=headers,
        files={"file": ("top.jpg", garment_images(CATALOG[0])[0], "image/jpeg")},
    )
    assert result.status_code == 201
    return result.json()


@pytest.mark.parametrize("live_api", ["mock", "local"], indirect=True)
def test_live_reanalysis_recovers_failed_record_and_preserves_manual_edits(live_api):
    client, provider, context = live_api
    _, headers = register(client)
    _, other_headers = register(client)
    garment = upload(client, headers)
    garment_id = UUID(garment["garment_id"])
    path = f"/api/v1/garments/{garment_id}"
    original = client.get(garment["image_url"], headers=headers).content
    engine = create_engine(context["database_url"])
    try:
        with Session(engine) as db:
            row = db.get(Garment, garment_id)
            row.analysis_status = "failed"
            row.attributes.category = None
            row.attributes.error = "previous analysis failed"
            db.commit()
        assert client.post(path + "/reanalyze", headers=other_headers).status_code == 404
        assert client.post(path + "/reanalyze").status_code == 401
        retried = client.post(path + "/reanalyze", headers=headers)
        assert retried.status_code == 200, retried.text
        body = retried.json()
        assert body["analysis_status"] == "done" and body["category"] == "top"
        assert body["model_version"] == (
            "mock-1" if provider == "mock" else "vision-baseline-0.2/demo/flat/demo-manual-category"
        )
        edits = {"category": "bottom", "color": "navy", "style_candidates": [], "thermal_level": None}
        assert client.patch(path, headers=headers, json=edits).status_code == 200
        body = client.post(path + "/reanalyze", headers=headers).json()
        assert {key: body[key] for key in edits} == edits
        assert body["source"] == "user" and body["analysis_status"] == "done"
        assert client.get(garment["image_url"], headers=headers).content == original
        with Session(engine) as db:
            row = db.get(Garment, garment_id)
            assert row.attributes.user_edited_fields == sorted(edits)
            assert row.attributes.raw_output["provider"] == provider
    finally:
        engine.dispose()


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
def test_concurrent_reanalysis_is_rejected_and_patch_waits_then_preserves_latest_edit(live_api):
    client, _, context = live_api
    user_id, headers = register(client)
    garment = upload(client, headers)
    garment_id = UUID(garment["garment_id"])
    path = f"/api/v1/garments/{garment_id}"
    engine = create_engine(context["database_url"])
    started, finish, patch_started = Event(), Event(), Event()

    class SlowVision:
        name = "slow"

        def extract(self, image_path, garment_id):
            started.set()
            assert finish.wait(10)
            result = MockVision().extract(image_path, garment_id)
            result.attributes.category = "shoes"
            result.attributes.thermal_level = 4
            return result

    def analyze():
        with Session(engine, expire_on_commit=False) as db:
            return GarmentService(db, LocalStorage(context["storage_dir"])).reanalyze(
                user_id, garment_id, SafeVision(SlowVision(), 12), 10 * 1024 * 1024
            )

    def patch():
        patch_started.set()
        return client.patch(path, headers=headers, json={"color": "pink", "status": "laundry"})

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(analyze)
            try:
                assert started.wait(5)
                second = client.post(path + "/reanalyze", headers=headers)
                assert second.status_code == 409 and second.json()["code"] == "GARMENT_BUSY"
                edited = executor.submit(patch)
                assert patch_started.wait(2)
                with pytest.raises(TimeoutError):
                    edited.result(timeout=0.2)
            finally:
                finish.set()
            assert first.result(timeout=5).analysis_status == "done"
            patch_response = edited.result(timeout=5)
            assert patch_response.status_code == 200
            assert patch_response.json()["category"] == "shoes"
            assert patch_response.json()["thermal_level"] == 4
        result = client.post(path + "/reanalyze", headers=headers)
        assert result.status_code == 200
        assert result.json()["color"] == "pink" and result.json()["status"] == "laundry"
    finally:
        finish.set()
        engine.dispose()
