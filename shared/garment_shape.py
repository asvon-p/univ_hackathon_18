"""Conservative garment length hints from the saved shape and product silhouette."""

import re

import numpy as np


def shape_hints(image, wearing_role=None, garment_type=None):
    kind = re.sub(r"[\s_-]+", " ", (garment_type or "").lower())
    alpha = np.asarray(image.convert("RGBA"))[..., 3]
    y, x = np.where(alpha > 200)
    # An opaque photo has no trustworthy silhouette. Do not use the photo frame.
    ratio = None
    if len(x) and (alpha < 32).sum() > alpha.size * 0.05:
        ratio = (np.ptp(y) + 1) / (np.ptp(x) + 1)
    bottom, outer = "unknown", "regular"
    if wearing_role == "bottom":
        if any(s in kind for s in ("shorts", "short pants", "반바지", "숏팬츠", "버뮤다")):
            bottom = "short"
        elif any(s in kind for s in ("cropped", "capri", "7부", "9부", "크롭", "skirt", "치마")):
            bottom = "unknown"
        elif any(s in kind for s in ("long pants", "full length", "긴바지")):
            bottom = "long"
        elif ratio is not None:
            bottom = "long" if ratio >= 1.3 else "short" if ratio <= 0.95 else "unknown"
        elif any(s in kind for s in ("jeans", "trousers", "slacks", "청바지", "슬랙스")):
            bottom = "long"
    elif wearing_role == "outerwear":
        if any(
            s in kind
            for s in ("long coat", "long puffer", "long padding", "롱코트", "롱패딩", "trench", "트렌치")
        ):
            outer = "long"
        elif ratio is not None and ratio >= 1.5:
            outer = "long"
    return {"wearing_role": wearing_role or "", "bottom_length": bottom, "outerwear_length": outer}
