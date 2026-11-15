"""GPU HTTP queue contract without loading weights or starting a live worker."""

import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import tryon_server
from backend.tests.api.test_body_photo import picture


@pytest.fixture
def gpu_api(tmp_path, monkeypatch):
    fake = SimpleNamespace(OUT_DIR=tmp_path)
    monkeypatch.setitem(sys.modules, "tryon_server.local_tryon", fake)
    monkeypatch.setattr(tryon_server, "local_tryon", fake, raising=False)
    monkeypatch.setenv("TRYON_API_KEY", "test-only-gpu-key")
    monkeypatch.delenv("TRYON_TIMING_FILE", raising=False)
    name = "tryon_server._queue_test_server"
    spec = importlib.util.spec_from_file_location(name, Path("tryon_server/server.py"))
    server = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, server)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "read_state", lambda: {"phase": "ready"})
    server.PIPELINE_READY.set()
    # No context manager: no lifespan/model/worker startup. This tests queued admissions.
    client = TestClient(server.app, headers={"X-API-Key": "test-only-gpu-key"})
    yield client, server
    client.close()


def submit(client, owner, request_id=None):
    data = {
        "owner_id": str(owner),
        "request_id": str(request_id or uuid4()),
        "category": "tops",
        "ephemeral": "true",
    }
    return client.post(
        "/tryon",
        data=data,
        files={
            "person": ("person.png", picture(), "image/png"),
            "garment": ("garment.png", picture(), "image/png"),
        },
    )


def test_gpu_retry_is_idempotent_and_cancel_returns_queued_slot(gpu_api):
    client, server = gpu_api
    owner, request_id = uuid4(), uuid4()
    first = submit(client, owner, request_id)
    assert first.status_code == 202
    again = submit(client, owner, request_id)
    assert again.status_code == 202 and again.json()["job_id"] == first.json()["job_id"]
    assert server.JOB_QUEUE.qsize() == 1
    busy = submit(client, owner)
    assert busy.status_code == 429 and busy.headers["retry-after"] == "2"
    second = submit(client, uuid4())
    assert second.status_code == 202
    jid = first.json()["job_id"]
    assert client.delete(f"/jobs/{jid}").status_code == 204
    assert client.get(f"/jobs/{jid}").status_code == 404
    assert server.JOB_QUEUE.qsize() == 1
    assert client.get("/jobs/" + second.json()["job_id"]).json()["queue_position"] == 1
    assert submit(client, owner).status_code == 202


def test_gpu_parallel_admission_enforces_bounds_and_internal_key(gpu_api):
    client, server = gpu_api
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda _: submit(client, uuid4()), range(10)))
    assert sorted(r.status_code for r in responses) == [202] * 8 + [429] * 2
    assert server.JOB_QUEUE.qsize() == len(server.JOBS) == 8
    assert client.get("/jobs", headers={"X-API-Key": "wrong"}).status_code == 401


def test_gpu_status_uses_measured_queue_estimate(gpu_api):
    client, server = gpu_api
    first, second = submit(client, uuid4()).json(), submit(client, uuid4()).json()
    assert first["eta"]["basis"] == "learning"
    for _ in range(6):
        server.TIMING.record(server.JOBS[first["job_id"]].params, 8)
    one = client.get("/jobs/" + first["job_id"]).json()["eta"]
    two = client.get("/jobs/" + second["job_id"]).json()["eta"]
    assert one["wait_seconds"] == 0 and two["wait_seconds"] == 8
    assert one["remaining_seconds"] == 8 and two["remaining_seconds"] == 16


@pytest.mark.parametrize("category,expected", [("tops", 202), ("bottoms", 422)])
def test_outer_preservation_contract_and_future_mode(gpu_api, category, expected):
    client, server = gpu_api
    response = client.post("/tryon", data={"category": category, "preserve_inner": "true",
        "remaining_categories": "outerwear", "ephemeral": "true"}, files={
        "person": ("p.png", picture(), "image/png"), "garment": ("g.png", picture(), "image/png")})
    assert response.status_code == expected
    if expected == 202:
        job = server.JOBS[response.json()["job_id"]]
        assert job.params["preserve_inner"] is True and job.remaining_categories == ("outerwear",)
