import builtins
import hashlib
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vision import pipeline, u2net


def test_black_input_is_finite_and_mask_retains_dimensions_and_soft_edges():
    tensor = u2net.input_tensor(Image.new("RGB", (31, 79), "black"))
    assert tensor.shape == (1, 3, 320, 320) and tensor.dtype == np.float32
    assert np.isfinite(tensor).all()
    prediction = np.linspace(0, 1, 320 * 320).reshape(1, 1, 320, 320)
    mask = np.asarray(u2net.output_mask(prediction, (71, 137)))
    assert mask.shape == (137, 71)
    assert mask.min() <= 2 and mask.max() >= 253 and ((mask > 0) & (mask < 255)).any()


@pytest.mark.parametrize(
    "prediction", [np.zeros((1, 1, 320, 320)), np.full((1, 1, 320, 320), np.nan), np.zeros((1, 3, 32, 32))]
)
def test_bad_output_is_rejected(prediction):
    with pytest.raises(ValueError):
        u2net.output_mask(prediction, (64, 64))


def test_soft_mask_does_not_premultiply_source_rgb():
    class Session:
        def run(self, *args):
            return [np.linspace(0, 1, 320 * 320).reshape(1, 1, 320, 320)]

    segmenter = u2net.U2NetCutout.__new__(u2net.U2NetCutout)
    segmenter.session, segmenter.input_name, segmenter.output_name = Session(), "image", "mask"
    original = Image.new("RGB", (64, 64), (243, 177, 91))
    cutout = segmenter.remove(original)
    assert cutout.mode == "RGBA" and cutout.size == original.size
    assert np.array_equal(np.asarray(cutout)[..., :3], np.asarray(original))
    low, high = cutout.getchannel("A").getextrema()
    assert low <= 2 and high >= 253


def test_corrupt_cached_weights_are_not_loaded_or_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("U2NET_HOME", str(tmp_path))
    existing = tmp_path / "u2net.onnx"
    existing.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        u2net.model_path()
    assert existing.read_bytes() == b"corrupt"


def test_download_verified_before_atomic_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("U2NET_HOME", str(tmp_path))
    monkeypatch.setattr(u2net, "MODEL_SHA256", hashlib.sha256(b"verified model").hexdigest())
    monkeypatch.setattr(u2net, "urlopen", lambda *a, **kw: BytesIO(b"bad download"))
    with pytest.raises(ValueError, match="checksum"):
        u2net.model_path()
    assert not list(tmp_path.rglob("*.onnx")) and not list(tmp_path.rglob("*.tmp"))
    monkeypatch.setattr(u2net, "urlopen", lambda *a, **kw: BytesIO(b"verified model"))
    assert u2net.model_path().read_bytes() == b"verified model"


def test_required_segmentation_failure_does_not_report_success(tmp_path, monkeypatch):
    path = tmp_path / "user" / "garment" / "original.jpg"
    path.parent.mkdir(parents=True)
    Image.new("RGB", (64, 64), "navy").save(path)

    def unavailable(*args):
        raise ImportError("dependency unavailable")

    monkeypatch.setattr(pipeline, "_get_cutout", unavailable)
    result = pipeline.extract(
        str(path), "garment", pipeline.Pipeline(classifier="demo", segmentation="rembg")
    )
    assert not result.success and result.attributes is None
    assert result.error == "background removal failed: ImportError"
    assert not (path.parent / "cutout.png").exists()


def test_real_model_never_imports_matting_or_jit_dependencies():
    import os

    if not os.getenv("VISION_SMOKE_IMAGE"):
        pytest.skip("Enable real model smoke test explicitly")
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"rembg", "pymatting", "numba"}, name
        return original(name, *args, **kwargs)

    # Keep this strict guard in place for both model loading and actual inference.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "__import__", guarded)
        model = u2net.U2NetCutout()
        with Image.open(Path(os.environ["VISION_SMOKE_IMAGE"])) as image:
            out = model.remove(image)
        mask = np.asarray(out.getchannel("A"))
        assert (mask == 0).mean() > 0.1 and (mask > 244).mean() > 0.1
