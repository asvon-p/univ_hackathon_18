"""Check trouser hems against body pose; never stretch fabric over bare legs."""

import numpy as np


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
        if (confidence[0, [hip, knee, ankle]] < 0).any() or (points[[hip, knee, ankle]] < 0).any():
            continue
        p = points[[hip, knee, ankle]] * [w - 1, h - 1]
        if p[2, 1] - p[0, 1] < h * 0.15:
            continue
        radius = max(2, round(np.linalg.norm(p[2] - p[0]) * 0.035))
        covered = []
        for t in np.linspace(0, 1, 41):
            a, b, fraction = (p[0], p[1], t * 2) if t < 0.5 else (p[1], p[2], (t - 0.5) * 2)
            x, y = np.round(a + (b - a) * fraction).astype(int)
            patch = labels[
                max(0, y - radius) : min(h, y + radius + 1), max(0, x - radius) : min(w, x + radius + 1)
            ]
            if patch.size and np.mean(patch == 6) > 0.45:
                covered.append(t)
        hems.append(max(covered, default=0))
    if len(hems) != 2:
        return {"status": "unverified", "expected": expected}
    valid = min(hems) >= 0.80 if expected == "long" else max(hems) <= 0.68
    return {
        "status": "matched" if valid else "mismatch",
        "expected": expected,
        "leg_coverage": [round(float(v), 3) for v in hems],
    }
