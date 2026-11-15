import json
from types import SimpleNamespace

import pytest

from tryon_server.timing import TimingHistory, forecast

PARAMS = {"category": "tops", "garment_photo_type": "auto", "num_timesteps": 30, "segmentation_free": True}


def job(owner, *, running=False, future=()):
    return SimpleNamespace(
        id=owner,
        owner_id=owner,
        params=PARAMS,
        remaining_categories=future,
        started_mono=100.0 if running else None,
        discarded=False,
        status="running" if running else "queued",
    )


def measured(seconds=10):
    history = TimingHistory()
    for _ in range(6):
        history.record(PARAMS, seconds)
    return history


def test_cold_start_does_not_invent_a_countdown():
    target = job("a")
    eta = forecast(target, None, [target], TimingHistory(), 100)
    assert eta["remaining_seconds"] is None and eta["basis"] == "learning"


def test_forecast_accounts_for_running_elapsed_fair_turns_and_remaining_outfits():
    a, b, c = job("a", running=True, future=("tops",)), job("b", future=("tops",)), job("c", future=("tops",))
    history = measured()
    # Five seconds remain in A1; B1,C1,A2,B2,C2 follow. The handoff overlaps other users' work.
    expected = [(a, 0, 35), (b, 5, 45), (c, 15, 55)]
    for target, wait, total in expected:
        eta = forecast(target, a, [b, c], history, 105)
        assert eta["wait_seconds"] == wait and eta["remaining_seconds"] == total
        assert eta["remaining_low_seconds"] <= total <= eta["remaining_high_seconds"]
        assert eta["basis"] == "measured" and eta["sample_count"] == 6


def test_cancellation_and_new_arrivals_change_eta():
    a, b, c = job("a", running=True), job("b"), job("c")
    history = measured()
    before = forecast(c, a, [b, c], history, 102)
    after = forecast(c, a, [c], history, 102)
    assert before["remaining_seconds"] - after["remaining_seconds"] == 10
    # A cancelled CUDA call still occupies the GPU until it actually finishes.
    a.discarded = True
    assert forecast(c, a, [c], history, 102)["wait_seconds"] == 8
    assert forecast(c, None, [c], history, 102)["wait_seconds"] == 0


def test_delayed_running_job_marks_all_waiting_estimates_uncertain():
    a, b = job("a", running=True), job("b")
    eta = forecast(b, a, [b], measured(), 120)
    assert eta["overdue"] and eta["wait_seconds"] > 0
    assert forecast(b, None, [b], measured(), 120)["overdue"] is False


def test_anonymous_history_survives_restart_and_invalidates_changed_gpu(tmp_path):
    path = tmp_path / "timing.json"
    history = TimingHistory(path)
    history.load("GPU-A-weights-v1")
    for seconds in range(1, 51):
        history.record(PARAMS, seconds)
    history.record(PARAMS, float("nan"))
    data = json.loads(path.read_text())
    assert set(data) == {"version", "profile", "buckets"}
    assert len(data["buckets"][0]["seconds"]) == 40
    restored = TimingHistory(path)
    restored.load("GPU-A-weights-v1")
    assert restored.estimate(PARAMS) == history.estimate(PARAMS)
    restored.load("GPU-B-weights-v1")
    assert restored.estimate(PARAMS) is None
    path.write_text("broken")
    restored.load("GPU-A-weights-v1")
    assert restored.estimate(PARAMS) is None


def test_category_fallback_does_not_mix_inference_settings():
    history = measured(8)
    assert history.estimate({**PARAMS, "category": "bottoms"}).seconds == pytest.approx(8)
    assert history.estimate({**PARAMS, "num_timesteps": 50}) is None
    for _ in range(6):
        history.record(PARAMS, 16)
    assert history.estimate(PARAMS).seconds > 12


def test_single_users_future_steps_include_frontend_handoff():
    a = job("a", future=("tops", "tops"))
    assert forecast(a, None, [a], measured(), 100)["remaining_seconds"] == 34


def test_layered_outer_has_separate_measured_cost_and_loads_legacy_cache(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"version": 1, "profile": "gpu", "buckets": [
        {"settings": list(TimingHistory.key(PARAMS))[:4], "seconds": [8]*6}]}))
    history = TimingHistory(path)
    history.load("gpu")
    assert history.estimate(PARAMS).seconds == pytest.approx(8)
    target = job("a", future=("outerwear",))
    assert forecast(target, None, [target], history, 100)["remaining_seconds"] is None
    for _ in range(6):
        history.record({**PARAMS, "preserve_inner": True}, 12)
    assert forecast(target, None, [target], history, 100)["remaining_seconds"] == 22
