"""Restore the existing inner top only inside a confidently detected jacket opening.

No face recognition, learned identity state, or disk I/O. Uncertain cases keep the
previous outfit intact rather than publish an outer layer that erases the chosen top.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def _open_panels(generated, labels, hole, projected, left, right, upper, lower):
    """Widen a collapsed opening by moving its plackets, never painting over them.

    Small, row-wise 2D displacement only; collars/outer silhouette stay anchored.
    Only a continuous observed gap and an explicit product opening enable it.
    """
    height, width = hole.shape
    span = right - left
    center = (left + right) / 2
    rows = []
    for y in range(max(0, int(upper)), min(height, int(lower) + 1)):
        target = np.flatnonzero(projected[y])
        actual = np.flatnonzero(hole[y] & (np.abs(np.arange(width) - center) < span * 0.22))
        if len(target) < 3 or len(actual) < 2:
            continue
        segments = np.split(actual, np.flatnonzero(np.diff(actual) > 1) + 1)
        gap = min(segments, key=lambda s: abs(float(np.median(s)) - float(np.median(target))))
        if len(gap) < 2 or gap[-1] - gap[0] > span * 0.4:
            continue
        rows.append((y, gap[0], gap[-1], target[0], target[-1]))
    if len(rows) < (lower - upper) * 0.4:
        return generated, labels, hole, 0
    values = np.array(rows, dtype=float)
    ys = np.arange(height)
    fields = [np.interp(ys, values[:, 0], values[:, col]) for col in range(1, 5)]
    actual_left, actual_right, target_left, target_right = [
        cv2.GaussianBlur(v[:, None], (1, 13), sigmaX=0, sigmaY=2).ravel() for v in fields
    ]
    # Fade from the collar to the chest and back to the hem; keep silhouette fixed.
    t = (ys - upper) / max(1, lower - upper)
    fade = np.minimum(np.clip((t - 0.08) / 0.15, 0, 1), np.clip((0.98 - t) / 0.12, 0, 1))
    fade *= (ys >= values[0, 0]) & (ys <= values[-1, 0])
    # An observed opening can stop above a coat's hem. Ease the fabric back to
    # its original position there rather than leave a horizontal seam.
    fade *= np.clip((values[-1, 0] - ys) / max(8, (lower - upper) * 0.12), 0, 1)
    shift_left = np.clip(actual_left - target_left, 0, span * 0.06) * fade
    shift_right = np.clip(target_right - actual_right, 0, span * 0.06) * fade
    active = (shift_left + shift_right) > 1.5
    if not active.any():
        return generated, labels, hole, 0
    map_y, map_x = np.indices((height, width), dtype="float32")
    x = np.arange(width)
    for y in np.flatnonzero(active):
        a, b = actual_left[y], actual_right[y]
        opened_left, opened_right = a - shift_left[y], b + shift_right[y]
        if not left < opened_left < opened_right < right:
            continue
        map_x[y] = np.interp(
            x, [0, left, opened_left, opened_right, right, width - 1], [0, left, a, b, right, width - 1]
        )
    rgb = cv2.remap(np.asarray(generated.convert("RGB")), map_x, map_y, cv2.INTER_LINEAR)
    labels = cv2.remap(labels.astype("uint8"), map_x, map_y, cv2.INTER_NEAREST)
    hole = cv2.remap(hole.astype("uint8"), map_x, map_y, cv2.INTER_NEAREST).astype(bool)
    return Image.fromarray(rgb), labels, hole, int(active.sum())


def guided_inner_top(before, generated, garment, before_labels, generated_labels, polygon=None):
    """Project the explicit product opening onto the torso, then retain the real inner pixels.

    This is a 2D correspondence, not a cloth simulation. No color-based opening rejection.
    """
    from PIL import ImageDraw

    top = before_labels == 3
    ys, xs = np.where(top)
    if len(xs) < top.size * 0.015 or before.size != generated.size:
        return before.copy(), {"status": "skipped", "reason": "inner_top_missing", "preserved_pixels": 0}
    left, right = np.percentile(xs, [1, 99])
    yy, xx = np.indices(top.shape)
    center = (left + right) / 2
    central = top & (np.abs(xx - center) < (right - left) * 0.12)
    cy, _ = np.where(central)
    if not len(cy):
        return before.copy(), {"status": "skipped", "reason": "alignment_unavailable", "preserved_pixels": 0}
    upper, lower = np.percentile(cy, [1, 99])
    # A coat can extend well below the inner shirt. Its product opening must
    # map to the generated coat's hem, not stop at the shirt's waistline.
    generated_clothing = np.isin(generated_labels, [3, 4])
    coat_y, _ = np.where(generated_clothing & (np.abs(xx - center) < (right - left) * 0.18))
    opening_lower = lower
    if len(coat_y):
        coat_bottom = float(np.percentile(coat_y, 99))
        if coat_bottom > lower + (lower - upper) * 0.2:
            opening_lower = coat_bottom
    if polygon:
        alpha = garment.convert("RGBA").getchannel("A")
        bbox = alpha.getbbox() or (0, 0, garment.width, garment.height)
        x0, y0, x1, y1 = bbox
        coords = [
            (
                left + (x * garment.width - x0) / max(1, x1 - x0) * (right - left),
                upper + (y * garment.height - y0) / max(1, y1 - y0) * (opening_lower - upper),
            )
            for x, y in polygon
        ]
    else:
        coords = [
            (center - (right - left) * 0.09, upper),
            (center + (right - left) * 0.09, upper),
            (center + (right - left) * 0.16, opening_lower),
            (center - (right - left) * 0.16, opening_lower),
        ]
    opening = Image.new("L", before.size)
    ImageDraw.Draw(opening).polygon(coords, fill=255)
    # The product's back collar lies above its opening. Bridge that offset only
    # along the narrow center neckline, never across either front panel.
    opening_start = min(y for _, y in coords)
    bridge_end = min(opening_start + 2, upper + (lower - upper) * 0.20)
    if bridge_end > upper:
        half = (right - left) * 0.045
        ImageDraw.Draw(opening).rectangle(
            (center - half, upper - (lower - upper) * 0.15, center + half, bridge_end), fill=255
        )
    projected = np.asarray(opening) > 0
    # Product coordinates are only an approximate 2D correspondence. Let a
    # continuous observed V-neck follow the rendered plackets, but never let a
    # color-connected print/hem run across the outer panels.
    envelope = (np.abs(xx - center) < (right - left) * 0.20) & (
        (yy >= upper - (lower - upper) * 0.15) & (yy <= opening_lower + 2)
    )
    hole = projected & (top | generated_clothing)
    contour_found = False
    # Use the user opening as an anchor, then follow the generated jacket boundary.
    # A rectangular torso projection alone leaves an invented white undershirt at
    # the neckline/hem. Restore those connected non-jacket pixels from the prior
    # outfit as well (skin/trousers included), without repainting jacket panels.
    rgba = np.asarray(garment.convert("RGBA"))
    solid = rgba[..., 3] > 243
    if solid.sum() > 100:
        lab_ref = cv2.cvtColor(rgba[..., :3].astype("float32") / 255, cv2.COLOR_RGB2LAB)
        colors = lab_ref[solid]
        colors = colors[np.linspace(0, len(colors) - 1, min(3000, len(colors))).astype(int)]
        cv2.setRNGSeed(42)
        _, counts, palette = cv2.kmeans(
            colors,
            min(5, len(colors)),
            None,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 25, 0.2),
            1,
            cv2.KMEANS_PP_CENTERS,
        )
        sizes = np.bincount(counts.ravel(), minlength=len(palette))
        palette = palette[sizes >= max(2, len(colors) * 0.03)]
        lab_out = cv2.cvtColor(
            np.asarray(generated.convert("RGB")).astype("float32") / 255, cv2.COLOR_RGB2LAB
        )
        distance = np.min(np.linalg.norm(lab_out[..., None, :] - palette, axis=-1), axis=-1)
        edit_area = np.isin(before_labels, [3, 4]) | np.isin(generated_labels, [3, 4])
        # The old top can extend beyond the new shoulder/sleeve silhouette.
        # Restoring generated background there leaves an ivory halo around blue fabric.
        non_jacket = (distance > 16) & edit_area & (generated_labels != 0)
        collar_shadow = (
            generated_clothing
            & (yy < upper + (lower - upper) * 0.25)
            & (lab_out[..., 0] < np.percentile(colors[:, 0], 10) * 0.7)
        )
        non_jacket &= ~collar_shadow
        non_jacket = cv2.morphologyEx(non_jacket.astype("uint8"), cv2.MORPH_CLOSE, np.ones((3, 3), "uint8"))
        count, components, stats, _ = cv2.connectedComponentsWithStats(non_jacket, 8)
        overlaps = np.bincount(components[projected & top].ravel(), minlength=count)
        overlaps[0] = 0
        component = int(np.argmax(overlaps))
        if component and overlaps[component] > max(15, hole.sum() * 0.15):
            candidate = components == component
            # Exclude background/parser leakage; retain a confidence-free geometric
            # fallback when fabric colors cannot distinguish the opening.
            if candidate.sum() < top.sum() * 0.6 and (candidate & envelope).any():
                candidate &= envelope
                widths = candidate.sum(axis=1)
                window = max(9, int((lower - upper) * 0.35) | 1)
                positive = np.flatnonzero(widths)
                smooth_widths = np.interp(np.arange(len(widths)), positive, widths[positive])
                typical = np.median(
                    np.lib.stride_tricks.sliding_window_view(
                        np.pad(smooth_widths, window // 2, mode="edge"), window
                    ),
                    axis=1,
                )
                for y in np.flatnonzero(widths):
                    columns = np.flatnonzero(candidate[y])
                    midpoint = float(np.median(columns))
                    if typical[y] > 0:
                        candidate[y] &= np.abs(np.arange(before.width) - midpoint) <= max(
                            3, typical[y] * 0.65
                        )
                # Do not punch a rectangular opening in solid fabric below the
                # observed gap. A long coat may close over the trousers there.
                hole = candidate
                contour_found = True
    if not contour_found and not polygon:
        # No confirmed product opening and no visible rendered gap: solid coat
        # fabric must occlude the outfit. Never invent a rectangular hole through
        # a closed/ambiguous long puffer merely to expose the selected shirt.
        return preserve_unedited_regions(before, generated, before_labels, generated_labels, "tops"), {
            "status": "approximate",
            "reason": "opening_unconfirmed",
            "preserved_pixels": 0,
            "contour_aligned": False,
            "panel_adjusted_rows": 0,
            "opening_bounded": True,
        }
    adjusted_rows = 0
    if contour_found and polygon:
        generated, generated_labels, hole, adjusted_rows = _open_panels(
            generated, generated_labels, hole, projected, left, right, upper, opening_lower
        )
        generated_clothing = np.isin(generated_labels, [3, 4])
    if contour_found:
        # A model can tuck its invented inner shirt into new jeans. Retain the
        # selected inner top's existing hem where it is actually exposed, without
        # painting across the generated outer panels or buttons.
        count, gaps, gap_stats, _ = cv2.connectedComponentsWithStats((~hole).astype("uint8"), 8)
        for component in range(1, count):
            x, y, w, h, area = gap_stats[component]
            if (
                0 < x
                and 0 < y
                and x + w < before.width
                and y + h < before.height
                and area <= max(9, min(36, before.width * 0.06))
            ):
                hole[gaps == component] = True
    # Hard anatomical boundary after all contour/hem/speckle operations. With no
    # reliable observed contour, fall back to the narrower product opening.
    hole &= envelope if contour_found else projected
    protect = np.isin(before_labels, [1, 2, 9, 11, 13, 15, 17])
    edit = np.isin(before_labels, [3, 4]) | np.isin(generated_labels, [3, 4])
    # An outer shirt can be shorter than its inner top. Keep the exposed hem
    # wherever the generator invents trousers/belt instead of outer fabric.
    edit &= ~(top & np.isin(generated_labels, [5, 6, 7]))
    edit = cv2.dilate(edit.astype("uint8"), np.ones((5, 5), "uint8")).astype(bool) & ~protect
    blend = cv2.GaussianBlur(edit.astype("float32"), (5, 5), 0.8)
    # Feather inward: never leak the inner knit's color over a button/placket edge.
    restore = cv2.GaussianBlur(hole.astype("float32"), (3, 3), 0.55)
    restore[~hole] = 0
    blend *= 1 - restore
    # The product's back collar can be hallucinated as a horizontal band across
    # the wearer's neck. Keep the previous neckline inside the central opening.
    # Product collars sit above the detected opening. For long coats their
    # projected offset is larger; bridge it to the existing neckline so a
    # generated skin/lining band cannot separate the collar from the shirt.
    neck_end = max(upper + (lower - upper) * 0.04, min(opening_start + 2, upper + (lower - upper) * 0.20))
    # Anchor the neck bridge to the actual narrow opening, not the full top width.
    # The latter erases shirt collars as a rectangular horizontal strip.
    near_neck = hole & (yy < upper + (lower - upper) * 0.22) & (yy >= upper)
    _, neck_x = np.where(near_neck)
    neck_center = float(np.median(neck_x)) if len(neck_x) else center
    neck_half = (
        min((right - left) * 0.055, max(2, float(np.ptp(neck_x)) * 0.35))
        if len(neck_x)
        else (right - left) * 0.055
    )
    neck = (yy < neck_end) & (np.abs(xx - neck_center) < neck_half) & projected
    if contour_found:
        # Observed skin/inner contours already reach the neckline. A geometric
        # bridge is only needed when fabric colors provide no usable contour.
        neck &= ~generated_clothing | (yy < upper)
    blend *= 1 - cv2.GaussianBlur(neck.astype("float32"), (5, 5), 0.8)
    blend[protect] = 0
    base = np.asarray(before.convert("RGB")).astype("float32")
    output = np.asarray(generated.convert("RGB")).astype("float32")
    result = Image.fromarray(
        np.round(base * (1 - blend[..., None]) + output * blend[..., None]).clip(0, 255).astype("uint8")
    )
    return result, {
        "status": "applied" if polygon else "approximate",
        "reason": "explicit_opening" if polygon else "estimated_opening",
        "preserved_pixels": int((restore > 0.99).sum()),
        "contour_aligned": contour_found,
        "panel_adjusted_rows": adjusted_rows,
        "opening_bounded": True,
    }


def preserve_unedited_regions(before, generated, before_labels, generated_labels, category):
    """A later top must not shorten the already selected trousers.

    New upper-clothing pixels can occlude lower clothing (long coat/long top),
    while pixels below/outside that silhouette stay exactly from the previous step.
    """
    if before.size != generated.size:
        raise ValueError("Compositing inputs must be aligned")
    old, new = before_labels, generated_labels
    if category == "bottoms":
        edit = np.isin(old, [5, 6, 7, 14]) | np.isin(new, [5, 6, 7, 14])
        protect = np.isin(old, [1, 2, 3, 9, 11, 12, 13, 15, 17])
    else:
        edit = np.isin(old, [3, 4, 10, 12, 16]) | np.isin(new, [3, 4, 10, 12, 16])
        protect = np.isin(old, [1, 2, 9, 11, 13, 15, 17])
        # Skin/arms hallucinated over trousers must not erase a completed bottom.
        edit &= ~np.isin(old, [5, 6, 7, 14]) | np.isin(new, [3, 4])
    edit = cv2.dilate(edit.astype("uint8"), np.ones((3, 3), "uint8")).astype(bool)
    edit &= ~protect
    alpha = cv2.GaussianBlur(edit.astype("float32"), (3, 3), 0.55)
    alpha[~edit] = 0
    alpha[protect] = 0
    base = np.asarray(before.convert("RGB"), dtype="float32")
    output = np.asarray(generated.convert("RGB"), dtype="float32")
    return Image.fromarray(
        np.round(base * (1 - alpha[..., None]) + output * alpha[..., None]).astype("uint8")
    )


def _largest(mask):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if count < 2:
        return np.zeros_like(mask, dtype=bool)
    return labels == (1 + np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def preserve_inner_top(before, generated, garment, before_labels, generated_labels, garment_labels):
    """Inputs are already aligned at the model's output resolution.

    The restoration follows the generated opening's color/segmentation contour,
    intersects the original top, and leaves the zipper boundary outside the mask.
    """
    fallback = {"status": "skipped", "reason": "opening_uncertain", "preserved_pixels": 0}
    if before.size != generated.size:
        return before.copy(), {**fallback, "reason": "alignment_unavailable"}
    base, output, reference = (np.asarray(im.convert("RGB")) for im in (before, generated, garment))
    top = before_labels == 3
    ys, xs = np.where(top)
    if len(xs) < base.shape[0] * base.shape[1] * 0.015:
        return before.copy(), {**fallback, "reason": "inner_top_missing"}
    left, right = np.percentile(xs, [1, 99])
    center, width = (left + right) / 2, right - left
    yy, xx = np.indices(top.shape)
    center_top = top & (np.abs(xx - center) < width * 0.1)
    cy, _ = np.where(center_top)
    if not len(cy):
        return before.copy(), fallback
    upper, lower = np.percentile(cy, [1, 99])
    torso = (np.abs(xx - center) < width * 0.24) & (yy >= upper) & (yy <= lower)
    palette_mask = garment_labels == 3
    lab_ref = cv2.cvtColor(reference.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    colors = lab_ref[palette_mask]
    if len(colors) < 100:
        return before.copy(), {**fallback, "reason": "outerwear_mask_missing"}
    # Ignore deepest folds/lining shadows when identifying the visible outer panels.
    colors = colors[colors[:, 0] >= np.percentile(colors[:, 0], 15)]
    colors = colors[np.linspace(0, len(colors) - 1, min(len(colors), 4000)).astype(int)]
    cv2.setRNGSeed(42)
    _, clusters, palette = cv2.kmeans(
        colors.astype(np.float32),
        min(6, len(colors)),
        None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.2),
        1,
        cv2.KMEANS_PP_CENTERS,
    )
    # Parsing can label the empty white opening as part of the product. Keep the
    # dominant fabric family, not that background or a bright zipper highlight.
    dominant = palette[np.argmax(np.bincount(clusters.ravel()))]
    palette = palette[np.linalg.norm(palette - dominant, axis=1) < 28]
    # A parser can include the shaded white product background in a light coat's
    # mask. Its bright, low-chroma cluster must not become a fabric match for the
    # white inner shirt. Keep this rejection limited to a chromatic main fabric.
    chroma = np.linalg.norm(palette[:, 1:], axis=1)
    dominant_chroma = float(np.linalg.norm(dominant[1:]))
    background_highlight = (
        (dominant_chroma > 12) & (palette[:, 0] > dominant[0] + 12) & (chroma < dominant_chroma * 0.55)
    )
    palette = palette[~background_highlight]
    lab_out = cv2.cvtColor(output.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
    distance = np.min(np.linalg.norm(lab_out[..., None, :] - palette, axis=-1), axis=-1)
    # Light outerwear can resemble exposed skin. A generated cropped inner top
    # may split the opening into shirt / bare waist / trousers. The parser's
    # torso and lower-clothing labels connect these non-jacket regions, only
    # inside the previous top and the narrow central search band.
    non_jacket = np.isin(generated_labels, [5, 6, 16])
    opening = top & torso & ((distance > 17) | non_jacket)
    opening = cv2.morphologyEx(opening.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    opening = _largest(opening.astype(bool) & top & torso)
    # Fill enclosed texture/zipper speckles without widening the external contour.
    flood = np.pad(opening.astype(np.uint8), 1)
    cv2.floodFill(flood, None, (0, 0), 1)
    opening |= (flood[1:-1, 1:-1] == 0) & top & torso
    # A hallucinated white undershirt hem may connect to the opening and flare
    # sideways below the zipper. Estimate width from the middle of the torso.
    row_widths = opening.sum(axis=1)
    middle = row_widths[int(upper + (lower - upper) * 0.2) : int(upper + (lower - upper) * 0.8)]
    positive = middle[middle > 0]
    typical = float(np.median(positive)) if len(positive) else 0
    if 0 < typical < width * 0.35:
        mid_x = float(np.median(np.where(opening)[1]))
        flared = row_widths > typical * 1.8
        opening[flared] &= (np.abs(xx - mid_x) < typical * 0.6)[flared]
        opening = _largest(opening)
    oy, ox = np.where(opening)
    if (
        len(ox) < len(xs) * 0.025
        or not len(oy)
        or np.ptp(oy) < (lower - upper) * 0.5
        or len(ox) > len(xs) * 0.30
        or np.ptp(ox) > width * 0.43
        or abs(float(np.median(ox)) - center) > width * 0.12
    ):
        return before.copy(), fallback
    opening = cv2.erode(opening.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1)
    # Protect original pixels outside the clothing edit, including face/hair/hands.
    edit = np.isin(before_labels, [3, 4]) | np.isin(generated_labels, [3, 4])
    edit = cv2.dilate(edit.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    edit &= ~np.isin(before_labels, [1, 2, 9, 11, 13])
    edit_alpha = cv2.GaussianBlur(edit.astype(np.float32), (5, 5), 0.8)
    edit_alpha[np.isin(before_labels, [1, 2, 9, 11, 13])] = 0
    # Keep the existing neckline in the central opening, avoiding a generated
    # collar/lining strip across bare skin above the inner top.
    neck = (yy < upper + (lower - upper) * 0.08) & (np.abs(xx - float(np.median(ox))) < typical * 0.52)
    neck_alpha = cv2.GaussianBlur(neck.astype(np.float32), (5, 5), 0.8)
    edit_alpha *= 1 - neck_alpha
    result = base.astype(np.float32) * (1 - edit_alpha[..., None]) + output * edit_alpha[..., None]
    restore_alpha = cv2.GaussianBlur(opening.astype(np.float32), (5, 5), 0.8)
    restore_alpha[~top] = 0
    result = result * (1 - restore_alpha[..., None]) + base * restore_alpha[..., None]
    image = Image.fromarray(np.round(result).clip(0, 255).astype(np.uint8))
    return image, {
        "status": "applied",
        "reason": None,
        "preserved_pixels": int((restore_alpha > 0.99).sum()),
        "opening_fraction": round(float(opening.sum() / len(xs)), 4),
    }
