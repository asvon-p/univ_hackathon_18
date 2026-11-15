from threading import BoundedSemaphore, Event
from types import SimpleNamespace

import pytest

from backend.app.core.errors import AppError
from backend.app.services import style_references as service
from recommendation.style_reference import compare
from shared.contracts import GarmentAttributes, ReferenceFeature


def test_timed_out_analysis_holds_capacity_until_worker_finishes(monkeypatch):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(service, "_analysis_slots", slots)
    started, finish, ended = Event(), Event(), Event()

    def work(*_):
        started.set()
        try:
            finish.wait(3)
            return (None, None)
        finally:
            ended.set()

    vision = SimpleNamespace(timeout_sec=0.03, _isolated_extract=work)
    try:
        assert service.extract_crop(vision, b"fixture", "u", "r", 100) is None
        assert started.wait(1)
        with pytest.raises(AppError) as caught:
            service.extract_crop(vision, b"fixture", "u", "r", 100)
        assert caught.value.status == 429
    finally:
        finish.set()
        assert ended.wait(2)
    assert slots.acquire(timeout=2)
    slots.release()


def test_color_vs_style_focus_changes_preference_and_unknown_is_not_a_match():
    feature = ReferenceFeature(category="top", color="red", style_candidates=["minimal"])
    red = GarmentAttributes(garment_id="red", category="top", color="red", style_candidates=["sporty"])
    blue = GarmentAttributes(garment_id="blue", category="top", color="blue", style_candidates=["minimal"])
    assert compare(feature, red, "color", set())[0] > compare(feature, blue, "color", set())[0]
    assert compare(feature, red, "style", set())[0] < compare(feature, blue, "style", set())[0]
    score, match = compare(
        feature, GarmentAttributes(garment_id="unknown", category="top"), "balanced", {"unknown"}
    )
    assert score == 0 and not match.matched and match.fixed


def test_reference_scratch_is_removed_normally_and_after_crash(tmp_path, monkeypatch):
    import os
    import time

    from backend.app.adapters.vision import MockVision, SafeVision
    from backend.app.services import reference_temporary
    from backend.tests.api.test_style_references import fixture_photo

    monkeypatch.setattr(reference_temporary, "work_root", lambda: tmp_path)
    vision = SafeVision(MockVision(), timeout_sec=2)
    vision._reference_extract(fixture_photo(), "audit-user", "audit-ref", 1000000)
    assert list(tmp_path.iterdir()) == []
    abandoned = tmp_path / "task-abandoned"
    abandoned.mkdir()
    (abandoned / "crop.jpg").write_bytes(b"synthetic")
    old = time.time() - 1801
    os.utime(abandoned, (old, old))
    active = tmp_path / "task-active"
    active.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    assert reference_temporary.expire_scratch() == 1
    assert not abandoned.exists() and active.exists() and unrelated.exists()
