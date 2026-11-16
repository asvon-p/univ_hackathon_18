from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from tryon_server import local_tryon
from tryon_server.garment_length import GarmentLengthError


def pipeline(monkeypatch):
    labels = np.zeros((192, 128), dtype="uint8")
    labels[40:100, 30:98] = 3
    labels[100:180, 40:88] = 6
    pipe = Mock(return_value=SimpleNamespace(images=[Image.new("RGB", (128, 192), "blue")]))
    pipe.device = SimpleNamespace(type="cpu")
    pipe.hp_model.predict.return_value = labels
    pipe.pose_model.return_value = {}
    monkeypatch.setattr(local_tryon, "get_pipeline", lambda _: pipe)
    return pipe


@pytest.mark.parametrize(
    ("category", "role", "length", "expected", "masked"),
    [
        ("bottoms", "bottom", "regular", "bottoms", True),
        ("tops", "top", "regular", "tops", False),
        ("tops", "outerwear", "regular", "tops", True),
        ("tops", "outerwear", "long", "one-pieces", True),
    ],
)
def test_masked_coverage_follows_role_and_length(monkeypatch, category, role, length, expected, masked):
    pipe = pipeline(monkeypatch)
    garment = Image.new("RGBA", (64, 128), "blue")
    garment.info.update(
        fitjang_wearing_role=role, fitjang_outerwear_length=length, fitjang_opening_mode="off"
    )
    result = local_tryon.run_tryon(Image.new("RGB", (256, 384)), garment, category, "flat-lay")
    assert pipe.call_args.kwargs["category"] == expected
    assert pipe.call_args.kwargs["segmentation_free"] is not masked
    assert result.images[0].size == (128, 192)


@pytest.mark.parametrize("second_status", ["matched", "mismatch"])
def test_length_retry_is_bounded_and_never_publishes_known_mismatch(monkeypatch, second_status):
    pipe = pipeline(monkeypatch)
    check = Mock(side_effect=[{"status": "mismatch"}, {"status": second_status}])
    monkeypatch.setattr("tryon_server.garment_length.check_bottom_length", check)
    garment = Image.new("RGBA", (64, 128), "blue")
    garment.info["fitjang_bottom_length"] = "long"
    args = (Image.new("RGB", (128, 192)), garment, "bottoms", "flat-lay")
    if second_status == "mismatch":
        with pytest.raises(GarmentLengthError):
            local_tryon.run_tryon(*args)
    else:
        result = local_tryon.run_tryon(*args)
        assert result.fit == {"status": "matched", "retried": True}
    assert pipe.call_count == 2
    assert all(c.kwargs["segmentation_free"] is False for c in pipe.call_args_list)
    assert pipe.call_args_list[1].kwargs["seed"] == pipe.call_args_list[0].kwargs["seed"] + 1
