"""Geometric fixtures only: no personal photographs or model downloads."""

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("cv2", reason="Run image compositing tests in the isolated GPU runtime")

from tryon_server.layering import preserve_inner_top


def scene(opened=True):
    base = np.full((200, 160, 3), 230, dtype=np.uint8)
    before = np.zeros((200, 160), dtype=np.uint8)
    before[40:160, 30:130] = 3
    before[10:35, 65:95] = 1
    # Distinct texture: restoration must keep exact RGB values, not just "white".
    base[40:160, 30:130] = (215, 201, 170)
    base[40:160:4, 30:130] = (240, 230, 210)
    output = base.copy()
    output[40:160, 30:130] = (73, 90, 42)
    output[:35] = 0  # Simulate unwanted model changes outside the clothing.
    if opened:
        output[40:160, 69:91] = (255, 255, 255)
        output[105:108, 79:82] = (73, 90, 42)  # Small enclosed texture/zipper speckle.
    garment = Image.new("RGB", (80, 80), (73, 90, 42))
    return (
        Image.fromarray(base),
        Image.fromarray(output),
        garment,
        before,
        before.copy(),
        np.full((80, 80), 3),
    )


def test_preserves_texture_inside_opening_and_protects_non_clothing():
    args = scene()
    result, info = preserve_inner_top(*args)
    actual, base = np.asarray(result), np.asarray(args[0])
    assert info["status"] == "applied" and info["preserved_pixels"] > 1000
    np.testing.assert_array_equal(actual[50:150, 74:86], base[50:150, 74:86])
    np.testing.assert_array_equal(actual[:30], base[:30])
    np.testing.assert_array_equal(actual[10:35, 65:95], base[10:35, 65:95])
    np.testing.assert_array_equal(actual[60:140, 40:60], np.asarray(args[1])[60:140, 40:60])


def test_flared_undershirt_hem_does_not_hide_an_otherwise_clear_opening():
    args = list(scene())
    output = np.asarray(args[1]).copy()
    output[155:160, 40:120] = 255
    args[1] = Image.fromarray(output)
    result, info = preserve_inner_top(*args)
    assert info["status"] == "applied"
    np.testing.assert_array_equal(np.asarray(result)[50:150, 74:86], np.asarray(args[0])[50:150, 74:86])


def test_skin_colored_gap_in_cropped_inner_top_is_restored_without_widening_jacket():
    args = list(scene())
    generated = np.asarray(args[1]).copy()
    generated[85:120, 69:91] = (73, 90, 42)  # Same color as the jacket, despite being bare waist.
    args[1] = Image.fromarray(generated)
    args[4][85:120, 69:91] = 16
    result, info = preserve_inner_top(*args)
    assert info["status"] == "applied"
    np.testing.assert_array_equal(np.asarray(result)[50:150, 74:86], np.asarray(args[0])[50:150, 74:86])
    np.testing.assert_array_equal(np.asarray(result)[60:140, 40:60], generated[60:140, 40:60])


def test_beige_fabric_palette_excludes_shaded_white_product_background():
    args = list(scene())
    garment = np.full((80, 80, 3), (170, 151, 110), dtype=np.uint8)
    garment[:, 28:52] = (225, 220, 207)  # Background mistakenly labeled as garment.
    args[2] = Image.fromarray(garment)
    output = np.asarray(args[1]).copy()
    output[40:160, 30:130] = (170, 151, 110)
    output[40:160, 69:91] = (246, 246, 244)
    args[1] = Image.fromarray(output)
    result, info = preserve_inner_top(*args)
    assert info["status"] == "applied"
    np.testing.assert_array_equal(np.asarray(result)[50:150, 74:86], np.asarray(args[0])[50:150, 74:86])


@pytest.mark.parametrize("problem", ["closed", "alignment", "no_top", "no_garment", "whole_panel"])
def test_uncertain_cases_keep_previous_outfit_and_report_skip(problem):
    args = list(scene(problem != "closed"))
    if problem == "alignment":
        args[1] = args[1].resize((80, 100))
    if problem == "no_top":
        args[3][:] = 0
    if problem == "no_garment":
        args[5][:] = 0
    if problem == "whole_panel":
        args[1] = Image.new("RGB", args[0].size, "white")
    result, info = preserve_inner_top(*args)
    assert info["status"] == "skipped" and info["preserved_pixels"] == 0
    np.testing.assert_array_equal(np.asarray(result), np.asarray(args[0]))
