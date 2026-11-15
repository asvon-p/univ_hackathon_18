import time

from backend.app.adapters.vision import MockVision, SafeVision
from shared.contracts import VisionResult


class Boom:
    name = "local"

    def extract(self, image_path, garment_id):
        raise RuntimeError("CUDA out of memory")


def test_missing_native_dependency_does_not_stop_other_features():
    class MissingDependency:
        name = "local"

        def warmup(self):
            raise ImportError("native dependency unavailable")

        def extract(self, *args):
            raise ImportError("native dependency unavailable")

    vision = SafeVision(MissingDependency(), timeout_sec=1)
    vision.warmup()
    assert vision.warmup_error == "ImportError"
    assert not vision.extract("unused.jpg", "G1").result.success


class Slow:
    name = "local"

    def extract(self, image_path, garment_id):
        time.sleep(0.5)
        return MockVision().extract(image_path, garment_id)


class NoAttrs:
    name = "local"

    def extract(self, image_path, garment_id):
        return VisionResult(success=True, attributes=None)


def test_mock_reads_filename():
    r = MockVision().extract("/x/outer_camel_coat_minimal.jpg", "G1")
    a = r.attributes
    assert a.category == "outerwear" and a.color == "camel" and "minimal" in a.style_candidates


def test_exception_becomes_failure():
    out = SafeVision(Boom(), timeout_sec=1).extract("a.jpg", "G1")
    assert not out.result.success and "CUDA" in out.result.error


def test_timeout_becomes_failure():
    out = SafeVision(Slow(), timeout_sec=0.1).extract("a.jpg", "G1")
    assert not out.result.success and "timeout" in out.result.error


def test_missing_attributes_is_failure():
    assert not SafeVision(NoAttrs(), timeout_sec=1).extract("a.jpg", "G1").result.success


def test_garment_id_forced_to_server_value():
    class WrongId:
        name = "local"

        def extract(self, image_path, garment_id):
            return MockVision().extract(image_path, "OTHER")

    out = SafeVision(WrongId(), timeout_sec=1).extract("top.jpg", "G1")
    assert out.result.attributes.garment_id == "G1"
