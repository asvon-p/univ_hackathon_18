"""Anonymous measured service times and a forecast of the fair single-GPU queue."""

from __future__ import annotations

import json
import math
import statistics
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True)
class Estimate:
    seconds: float
    low: float
    high: float
    samples: int


class TimingHistory:
    """Bounded samples contain durations/settings only: no images, users or job IDs."""

    def __init__(self, path: Path | None = None):
        self.path, self.profile = path, None
        self.samples = {}
        self.lock = RLock()

    @staticmethod
    def key(params):
        return (
            params.get("category", "tops"),
            params.get("garment_photo_type", "auto"),
            int(params.get("num_timesteps", 30)),
            bool(params.get("segmentation_free", True)),
            bool(params.get("preserve_inner", False)),
        )

    def load(self, profile):
        with self.lock:
            self.profile, self.samples = profile, {}
            if self.path is None:
                return
            try:
                if self.path.stat().st_size > 128_000:
                    return
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("version") != 1 or data.get("profile") != profile:
                    return
                for row in data["buckets"][:128]:
                    key = tuple(row["settings"])
                    if len(key) == 4:
                        key = (*key, False)  # Preserve measured legacy/base inference timings.
                    values = row["seconds"][-40:]
                    if len(key) == 5 and all(
                        isinstance(v, (float, int)) and math.isfinite(v) and 0 < v <= 900 for v in values
                    ):
                        self.samples[key] = deque(values, maxlen=40)
            except (OSError, ValueError, TypeError, KeyError):
                self.samples = {}

    def record(self, params, seconds):
        if not math.isfinite(seconds) or not 0 < seconds <= 900:
            return
        with self.lock:
            key = self.key(params)
            if key not in self.samples and len(self.samples) >= 128:
                self.samples.pop(next(iter(self.samples)))
            self.samples.setdefault(key, deque(maxlen=40)).append(seconds)
            if self.path is None:
                return
            data = {
                "version": 1,
                "profile": self.profile,
                "buckets": [{"settings": list(k), "seconds": list(v)} for k, v in self.samples.items()],
            }
            temporary = self.path.with_suffix(".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(data), encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                pass  # RAM estimates still work if the statistics cache is unavailable.

    def estimate(self, params):
        with self.lock:
            key = self.key(params)
            values = list(self.samples.get(key, []))
            if not values:
                # Same inference settings on this GPU; category fallback until its first sample.
                values = [v for k, rows in self.samples.items() if k[1:] == key[1:] for v in rows][-40:]
            if not values:
                return None
            # Recent values respond to load changes, while a range avoids false precision.
            weights = [0.85**i for i in reversed(range(len(values)))]
            mean = sum(v * w for v, w in zip(values, weights, strict=True)) / sum(weights)
            spread = max(1.0, mean * (0.25 if len(values) < 5 else 0.15), statistics.pstdev(values) * 1.5)
            return Estimate(mean, max(1.0, mean - spread), mean + spread, len(values))


def forecast(target, running, queued, history, now, handoff_seconds=2.0):
    """Simulate ready-user turns including each admitted outfit's remaining garments.

    Future stages become ready after one frontend polling interval. New arrivals after
    this snapshot cannot be predicted. A cancelled running CUDA step still consumes time.
    """
    if target.status not in ("queued", "running"):
        return None
    active = ([running] if running is not None else []) + list(queued)
    estimates = {}
    for job in active:
        estimates[job.id] = [history.estimate(job.params)] + [
            history.estimate({**job.params, "category": "tops" if c == "outerwear" else c,
                              "preserve_inner": c == "outerwear"})
            for c in job.remaining_categories
        ]
    if target.id not in estimates or any(e is None for rows in estimates.values() for e in rows):
        return {
            "basis": "learning",
            "wait_seconds": None,
            "remaining_seconds": None,
            "remaining_low_seconds": None,
            "remaining_high_seconds": None,
            "step_seconds": None,
            "step_elapsed_seconds": None,
            "sample_count": 0,
            "overdue": False,
        }

    def simulate(metric):
        clock, wait, finish = 0.0, None, None
        ready, arrivals = deque(), []
        previous = running.owner_id or running.id if running else None

        def following(job, index, at):
            if not job.discarded and index + 1 < len(estimates[job.id]):
                arrivals.append((at + handoff_seconds, job, index + 1))

        if running:
            elapsed = max(0, now - running.started_mono)
            duration = getattr(estimates[running.id][0], metric)
            clock = max(1.0, duration - elapsed, duration * 0.15 if elapsed > duration else 0)
            if running.id == target.id:
                wait, finish = 0.0, clock
            following(running, 0, clock)
        ready.extend((job, 0) for job in queued)
        while ready or arrivals:
            arrivals.sort(key=lambda row: row[0])
            if not ready:
                clock = max(clock, arrivals[0][0])
            while arrivals and arrivals[0][0] <= clock:
                _, job, index = arrivals.pop(0)
                ready.append((job, index))
            if len(ready) > 1 and (ready[0][0].owner_id or ready[0][0].id) == previous:
                ready.rotate(-1)
            job, index = ready.popleft()
            if job.id == target.id and index == 0:
                wait = clock
            clock += getattr(estimates[job.id][index], metric)
            if job.id == target.id:
                finish = clock
            following(job, index, clock)
            previous = job.owner_id or job.id
        return wait, finish

    wait, remaining = simulate("seconds")
    _, low = simulate("low")
    _, high = simulate("high")
    sample = estimates[target.id][0]
    elapsed = max(0, now - target.started_mono) if target.started_mono is not None else 0
    overdue = running is not None and now - running.started_mono > estimates[running.id][0].high
    return {
        "basis": "measured" if sample.samples >= 5 else "warming",
        "wait_seconds": math.ceil(wait),
        "remaining_seconds": math.ceil(remaining),
        "remaining_low_seconds": math.ceil(min(low, remaining)),
        "remaining_high_seconds": math.ceil(max(high, remaining)),
        "step_seconds": round(sample.seconds, 2),
        "step_elapsed_seconds": round(elapsed, 2),
        "sample_count": sample.samples,
        "overdue": overdue,
    }
