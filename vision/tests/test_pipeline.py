from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import vision
from shared.contracts import VisionResult
from vision import pipeline


@pytest.fixture(autouse=True)
def fast_mode(monkeypatch):
    monkeypatch.setenv("VISION_CLASSIFIER", "demo")
    monkeypatch.setenv("VISION_SEGMENTATION", "flat")
    monkeypatch.setenv("VISION_DEMO_CATEGORY", "top")


def test_missing_file_returns_failure(tmp_path):
    result = vision.extract(str(tmp_path / "no.jpg"), "G1")
    assert isinstance(result, VisionResult)
    assert result.success is False


def test_corrupt_image_returns_failure(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    assert vision.extract(str(bad), "G1").success is False


def _sample(tmp_path: Path) -> Path:
    folder = tmp_path / "user-a" / "G1"
    folder.mkdir(parents=True)
    image = Image.new("RGB", (160, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((42, 35, 118, 140), fill=(230, 20, 20))
    path = folder / "original.jpg"
    image.save(path)
    return path


def test_demo_extract_cutout_and_color(tmp_path):
    img = _sample(tmp_path)
    result = vision.extract(str(img), "G1")
    assert result.success, result.error
    assert result.attributes.garment_id == "G1"
    assert result.attributes.category == "top"
    assert result.attributes.color == "red"
    assert result.attributes.cutout_path == "user-a/G1/cutout.png"
    with Image.open(img.parent / "cutout.png") as cutout:
        assert cutout.format == "PNG"
        assert cutout.mode == "RGBA"
        assert cutout.getpixel((0, 0))[3] == 0
        assert cutout.getpixel((80, 80))[3] == 255


def test_demo_category_can_be_selected_for_rehearsal(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_DEMO_CATEGORY", "bottom")
    result = vision.extract(str(_sample(tmp_path)), "G1")
    assert result.success
    assert result.attributes.category == "bottom"
    assert result.attributes.subcategory is None


def test_simple_segmentation_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_SEGMENTATION", "off")
    result = vision.extract(str(_sample(tmp_path)), "G1")
    assert result.success
    assert result.attributes.cutout_path is None
    assert not (tmp_path / "user-a" / "G1" / "cutout.png").exists()


def test_clip_classifier_is_interchangeable(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_CLASSIFIER", "clip")
    monkeypatch.setattr(
        pipeline, "_clip_model", lambda *args: (_ for _ in ()).throw(RuntimeError("model unavailable"))
    )
    result = vision.extract(str(_sample(tmp_path)), "G1")
    assert not result.success
    assert "model unavailable" in result.error


def test_does_not_infer_style_or_thickness_from_demo_image(tmp_path):
    result = vision.extract(str(_sample(tmp_path)), "G1")
    assert result.attributes.pattern == "unknown"
    assert result.attributes.style_candidates == []
    assert result.attributes.thermal_level is None
