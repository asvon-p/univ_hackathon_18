"""Check trouser hems against body pose; never stretch fabric over bare legs."""

import numpy as np

# Allow a small pose/parser tolerance at the ankle, not a calf-length hem.
ANKLE_COVERAGE = 0.95


class GarmentLengthError(RuntimeError):
    code = "GARMENT_LENGTH_MISMATCH"


def check_bottom_length(labels, pose, expected):
    if expected not in ("long", "short"):
        return {"status": "unverified", "expected": "unknown"}
    body = pose.get("bodies", {})
    points = np.asarray(body.get("candidate", []))
    confidence = np.asarray(body.get("subset", []))
    if points.shape != (18, 2) or confidence.shape != (1, 18):
        return {"status": "unverified", "expected": expected}
    h, w = labels.shape
    hems = []
    for hip, knee, ankle in ((8, 9, 10), (11, 12, 13)):
        leg = points[[hip, knee, ankle]]
        if (
            (confidence[0, [hip, knee, ankle]] < 0).any()
            or not np.isfinite(leg).all()
            or (leg < 0).any()
            or (leg > 1).any()
        ):
            continue
        p = leg * [w - 1, h - 1]
        if p[2, 1] - p[0, 1] < h * 0.15:
            continue
        radius = max(1, round(np.linalg.norm(p[2] - p[0]) * 0.015))
        covered = []
        for t in np.linspace(0, 1, 81):
            a, b, fraction = (p[0], p[1], t * 2) if t < 0.5 else (p[1], p[2], (t - 0.5) * 2)
            x, y = np.round(a + (b - a) * fraction).astype(int)
            patch = labels[
                max(0, y - radius) : min(h, y + radius + 1), max(0, x - radius) : min(w, x + radius + 1)
            ]
            if patch.size and np.mean(patch == 6) > 0.45:
                covered.append(t)
        # A stray pants-labelled patch near the ankle cannot turn otherwise
        # bare calves into a verified full-length trouser leg.
        if expected == "long" and sum(0.55 <= t <= 0.95 for t in covered) < 26:
            hems.append(0.0)
        else:
            hems.append(max(covered, default=0))
    if len(hems) != 2:
        return {"status": "unverified", "expected": expected}
    valid = min(hems) >= ANKLE_COVERAGE if expected == "long" else max(hems) <= 0.68
    return {
        "status": "matched" if valid else "mismatch",
        "expected": expected,
        "leg_coverage": [round(float(v), 3) for v in hems],
    }
