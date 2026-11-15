"""Conservative, local product-image opening detection. No person images or external API."""

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, median_filter

from shared.garment_layers import validate_opening

VERSION = "product-opening-v2"


def detect_opening(image):
    """Find a long central gap/contrasting lining bounded by two jacket panels.

    Returns normalized, editable coordinates, never changes the source image.
    Closed coats / neck holes / uncertain color boundaries are left intact.
    """
    unknown = {
        "points": [],
        "status": "needs_review",
        "confidence": 0.0,
        "reason": "opening_not_clear",
        "detector": VERSION,
    }
    small = image.convert("RGBA")
    small.thumbnail((384, 512), Image.Resampling.LANCZOS)
    rgba = np.asarray(small)
    foreground = rgba[..., 3] > 160
    ys, xs = np.where(foreground)
    if len(xs) < foreground.size * 0.08:
        return unknown
    x0, x1 = np.percentile(xs, [1, 99])
    y0, y1 = np.percentile(ys, [1, 99])
    width, height = x1 - x0, y1 - y0
    center = (x0 + x1) / 2
    if width < 30 or height < 50:
        return unknown
    rgb = gaussian_filter(rgba[..., :3].astype(float), sigma=(1.2, 1.2, 0))
    yy, xx = np.indices(foreground.shape)
    middle = (yy > y0 + height * 0.25) & (yy < y0 + height * 0.7)
    # A real opening can have partial alpha; do not require it to be opaque.
    seed_mask = middle & (abs(xx - center) < width * 0.025)
    left_mask = middle & (xx > center - width * 0.32) & (xx < center - width * 0.16) & foreground
    right_mask = middle & (xx > center + width * 0.16) & (xx < center + width * 0.32) & foreground
    candidate = rgba[..., 3] < 80
    method = "alpha_gap"
    # Lining can remain opaque after background segmentation. Only a central
    # color distinct from BOTH visible panels may augment the transparent gap.
    if min(seed_mask.sum(), left_mask.sum(), right_mask.sum()) > 40:
        inner = np.median(rgb[seed_mask], axis=0)
        left = np.median(rgb[left_mask], axis=0)
        right = np.median(rgb[right_mask], axis=0)
        contrast = min(np.linalg.norm(inner - left), np.linalg.norm(inner - right))
        if contrast > 40 and np.linalg.norm(left - right) < 90:
            candidate |= np.linalg.norm(rgb - inner, axis=2) < min(55, contrast * 0.45)
            method = "alpha_and_lining"
    rows = []
    for y in range(max(0, int(y0 + height * 0.04)), min(small.height, int(y1 - height * 0.02))):
        lo, hi = max(0, int(center - width * 0.28)), min(small.width, int(center + width * 0.28) + 1)
        segments = np.split(
            np.flatnonzero(candidate[y, lo:hi]) + lo,
            np.flatnonzero(np.diff(np.flatnonzero(candidate[y, lo:hi])) > 1) + 1,
        )
        segments = [
            s for s in segments if len(s) and s[0] <= center + width * 0.06 and s[-1] >= center - width * 0.06
        ]
        if not segments:
            continue
        segment = max(segments, key=len)
        gap_left, gap_right = int(segment[0]), int(segment[-1])
        # Actual fabric must bound both sides; background around a closed coat
        # or below its hem must never be interpreted as an opening.
        if not width * 0.035 < gap_right - gap_left < width * 0.48:
            continue
        flank = max(3, int(width * 0.055))
        if gap_left < flank or gap_right + flank >= small.width:
            continue
        if (
            foreground[y, gap_left - flank : gap_left].mean() < 0.65
            or foreground[y, gap_right + 1 : gap_right + flank + 1].mean() < 0.65
        ):
            continue
        rows.append((y, gap_left, gap_right))
    if len(rows) < height * 0.38:
        return unknown
    rows = np.array(rows, dtype=float)
    groups = np.split(rows, np.flatnonzero(np.diff(rows[:, 0]) > height * 0.065) + 1)
    rows = max(groups, key=len)
    upper, lower = rows[0, 0], rows[-1, 0]
    coverage = len(rows) / max(1, lower - upper + 1)
    if (
        lower - upper < height * 0.48
        or upper > y0 + height * 0.38
        or lower < y0 + height * 0.68
        or coverage < 0.72
    ):
        return unknown
    # Fit an INSCRIBED trapezoid. This keeps lapels/panels outside the cut rather
    # than taking a convex hull that can cut through inward-curving jacket fabric.
    left_edge = median_filter(rows[:, 1], size=7)
    right_edge = median_filter(rows[:, 2], size=7)
    lf = np.polyfit(rows[:, 0], left_edge, 1)
    rf = np.polyfit(rows[:, 0], right_edge, 1)
    lf[1] += max(0, float(np.percentile(left_edge - np.polyval(lf, rows[:, 0]), 90))) + 1
    rf[1] -= max(0, float(np.percentile(np.polyval(rf, rows[:, 0]) - right_edge, 90))) + 1
    bounds = [
        (np.polyval(lf, upper), upper),
        (np.polyval(rf, upper), upper),
        (np.polyval(rf, lower), lower),
        (np.polyval(lf, lower), lower),
    ]
    if min(bounds[1][0] - bounds[0][0], bounds[2][0] - bounds[3][0]) < width * 0.025:
        return unknown
    points = [
        [round(float(x / (small.width - 1)), 6), round(float(y / (small.height - 1)), 6)] for x, y in bounds
    ]
    try:
        validate_opening(points)
    except ValueError:
        return unknown
    return {
        "points": points,
        "status": "detected",
        "confidence": round(min(0.96, coverage * 0.75 + 0.15), 2),
        "reason": method,
        "detector": VERSION,
    }
