"""Refine garment alpha; explicit user polygon cuts lining out without guessing fabric colors."""

import json
import math
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, PngImagePlugin


def validate_opening(points):
    if not isinstance(points, (list, tuple)) or len(points) > 24:
        raise ValueError("영역은 최대 24점으로 지정하세요")
    if points and len(points) < 3:
        raise ValueError("영역은 세 점 이상으로 지정하세요")
    if any(not isinstance(p, (tuple, list)) or len(p) != 2 for p in points):
        raise ValueError("좌표 형식이 잘못되었습니다")
    if any(
        not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for p in points for v in p
    ):
        raise ValueError("사진 안의 점을 선택하세요")
    if points:
        contour = np.array(points, dtype=float)
        following = np.roll(contour, -1, axis=0)
        edges = following - contour
        following_edges = np.roll(edges, -1, axis=0)
        cross = edges[:, 0] * following_edges[:, 1] - edges[:, 1] * following_edges[:, 0]
        area = abs(np.sum(contour[:, 0] * following[:, 1] - contour[:, 1] * following[:, 0])) / 2
        convex = (cross > 0).all() or (cross < 0).all()
        for start, edge_vector in zip(contour, edges, strict=True):
            relative = contour - start
            sides = edge_vector[0] * relative[:, 1] - edge_vector[1] * relative[:, 0]
            convex = convex and ((sides >= -1e-10).all() or (sides <= 1e-10).all())
        if not convex or not 0.002 <= area <= 0.65:
            raise ValueError("겹치지 않는 볼록 영역으로 안쪽 부분만 선택하세요")
    return points


def restore_source_rgb(cutout, original):
    """Reuse aligned source RGB; some segmenters darken RGB with a soft alpha mask.

    Never resize or guess alignment for a third-party cutout. Preserve its alpha.
    """
    if cutout.size != original.size:
        return cutout
    pixels = np.asarray(cutout.convert("RGBA"))
    rgb = np.asarray(original.convert("RGB"))
    solid = pixels[..., 3] > 244
    if solid.sum() < 100:
        return cutout
    delta = np.max(np.abs(pixels[..., :3].astype(float) - rgb), axis=2)
    if np.mean(delta[solid] < 24) < 0.9:
        return cutout
    result = original.convert("RGBA")
    result.putalpha(cutout.getchannel("A"))
    return result


def refined_layer(image, polygon=(), *, opening_mode="legacy", wearing_role=None, garment_type=None):
    from shared.garment_shape import shape_hints

    hints = shape_hints(image, wearing_role, garment_type)
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    # Remove isolated one-pixel noise and feather only the edge, retaining solid fabric.
    alpha = alpha.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.65))
    values = np.asarray(alpha).copy()
    values[values < 12] = 0
    values[values > 243] = 255
    if polygon:
        hole = Image.new("L", rgba.size)
        ImageDraw.Draw(hole).polygon(
            [(round(x * (rgba.width - 1)), round(y * (rgba.height - 1))) for x, y in polygon], fill=255
        )
        hole = np.asarray(hole.filter(ImageFilter.GaussianBlur(0.65)))
        values = np.minimum(values, 255 - hole)
    rgba.putalpha(Image.fromarray(values))
    # Extend nearest opaque RGB into partially transparent boundary to remove pale halos.
    from scipy.ndimage import distance_transform_edt

    pixels = np.asarray(rgba).copy()
    edge = (values > 0) & (values < 244)
    if edge.any() and (values >= 244).any():
        distances, indices = distance_transform_edt(values < 244, return_indices=True)
        # Decontaminate only the immediate edge. Filling a wide soft opening with
        # the nearest panel color invents fabric across an otherwise open shirt.
        edge &= distances <= max(2, min(5, min(rgba.size) * 0.003))
        pixels[edge, :3] = pixels[indices[0][edge], indices[1][edge], :3]
    result = Image.fromarray(pixels)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("fitjang_opening", json.dumps(polygon))
    metadata.add_text("fitjang_opening_mode", opening_mode)
    for name, value in hints.items():
        metadata.add_text("fitjang_" + name, value)
    output = BytesIO()
    result.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()
