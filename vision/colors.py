"""Foreground palette voting, robust to shading and small trim details."""

from __future__ import annotations

import colorsys

import numpy as np
from PIL import Image, ImageFilter


def _name(rgb, *, denim=False):
    r, g, b = np.asarray(rgb, dtype=float) / 255
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.19:
        return "black"
    # Separate warm off-white from neutral white. Use brightness corrected by
    # cluster statistics below, not the dark folds of an otherwise white shirt.
    if s < 0.13:
        if v >= 0.77:
            return "ivory" if r - b > 0.035 and r >= g >= b else "white"
        return "gray"
    if v < 0.40 and s < 0.30:
        return "gray"
    if 0.06 <= h < 0.19 and s < 0.44 and v >= 0.70:
        if v >= 0.86 and s < 0.23:
            return "ivory"
        return "beige"
    if 0.045 <= h < 0.16 and v < 0.73:
        return "camel" if v >= 0.55 and s >= 0.35 else "brown"
    if 0.16 <= h < 0.30 and s < 0.72:
        return "olive" if h >= 0.19 or v < 0.55 else "khaki"
    if h < 0.042 or h >= 0.96:
        return "burgundy" if v < 0.53 else ("pink" if s < 0.47 else "red")
    if h < 0.10:
        return "orange"
    if h < 0.17:
        return "yellow"
    if h < 0.46:
        return "green"
    if h < 0.72:
        if denim:
            return "denim"
        if v < 0.55:
            return "navy"
        return "sky_blue" if v >= 0.73 and s < 0.55 else "blue"
    if h < 0.84:
        return "purple"
    return "pink" if v >= 0.53 else "burgundy"


def dominant_color(image: Image.Image, cutout: Image.Image | None, *, denim=False) -> str:
    source = cutout.copy() if cutout is not None else image.convert("RGBA")
    source.thumbnail((256, 256))
    pixels = np.asarray(source.convert("RGB"))
    if cutout is not None:
        # Erode translucent antialiased borders so white-background spill is excluded.
        mask = np.asarray(source.getchannel("A").filter(ImageFilter.MinFilter(3))) > 220
        sample = pixels[mask]
    else:
        h, w = pixels.shape[:2]
        sample = pixels[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4].reshape(-1, 3)
    if len(sample) < 20:
        return "unknown"
    # Group chromatic direction independently of brightness: folds/highlights
    # remain part of the same garment color rather than becoming extra colors.
    values = sample.astype(float) / 255
    high, low = values.max(axis=1), values.min(axis=1)
    chroma = high - low
    hue = np.zeros(len(sample))
    active = chroma > 0.0001
    for channel, offset in [(0, 0), (1, 2), (2, 4)]:
        selected = active & (values.argmax(axis=1) == channel)
        hue[selected] = (
            (values[selected, (channel + 1) % 3] - values[selected, (channel + 2) % 3]) / chroma[selected]
            + offset
        ) % 6
    saturation = chroma / np.maximum(high, 0.001)
    bins = (np.floor((hue + 0.25) % 6 * 2)).astype(int)
    bins[saturation < 0.13] = 12
    # Preserve achromatic contrast, e.g. black/white stripes, without labeling
    # ordinary midtone fabric shadows as a second color.
    bins[(saturation < 0.13) & (high < 0.19)] = 13
    candidates = []
    for bucket in np.unique(bins):
        cluster = sample[bins == bucket]
        share = len(cluster) / len(sample)
        if share < 0.04:
            continue
        brightness = cluster.max(axis=1)
        lo, hi = np.percentile(brightness, [45, 90])
        core = cluster[(brightness >= lo) & (brightness <= hi)]
        rgb = np.median(core if len(core) else cluster, axis=0)
        candidates.append((_name(rgb, denim=denim), share, rgb))
    if not candidates:
        return "unknown"
    # Adjacent hue bins and shades of one color can vote for the same label.
    votes = {}
    for name, share, _ in candidates:
        votes[name] = votes.get(name, 0) + share
    ranked = sorted(votes, key=votes.get, reverse=True)
    if len(ranked) >= 2 and votes[ranked[0]] < 0.68 and votes[ranked[1]] >= 0.25:
        first, second = ranked[:2]
        same_family = [
            set(("black", "gray", "navy")),
            set(("white", "ivory", "beige")),
            set(("blue", "sky_blue", "navy", "denim")),
            set(("olive", "khaki", "green")),
            set(("brown", "camel", "beige")),
        ]
        if not any({first, second} <= family for family in same_family):
            return "multi"
    return ranked[0]
