import time
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.vision import SafeVision
from backend.app.db.models import Garment, GarmentAttribute
from backend.app.deps import get_vision
from backend.tests.api.test_garments import FailingVision, image_bytes, upload
from shared.contracts import GarmentAttributes, VisionResult


class NewVision:
    name = "test-reanalysis"

    def extract(self, image_path, garment_id):
        folder = Path(image_path).parent
        (folder / "cutout.png").write_bytes(image_bytes())
        return VisionResult(
            success=True,
            attributes=GarmentAttributes(
                garment_id=garment_id,
                category="shoes",
                subcategory="boots",
                color="black",
                pattern="solid",
                style_candidates=["formal"],
                thermal_level=4,
                cutout_path=f"{folder.parent.name}/{garment_id}/cutout.png",
            ),
            model_version="retry-v2",
        )


@pytest.fixture
def failed(garment_client):
    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(FailingVision(), 1)
    result = upload(garment_client)
    assert result.status_code == 201 and result.json()["analysis_status"] == "failed"
    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(NewVision(), 1)
    return result.json()


def path(item):
    return f"/api/v1/garments/{item['garment_id']}"


def test_failed_analysis_can_be_retried_without_reupload(garment_client, failed, db_session, storage):
    original = garment_client.get(failed["image_url"]).content
    result = garment_client.post(path(failed) + "/reanalyze")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["garment_id"] == failed["garment_id"] and body["image_url"] == failed["image_url"]
    assert body["analysis_status"] == "done" and body["error"] is None
    assert body["category"] == "shoes" and body["model_version"] == "retry-v2"
    assert body["source"] == "model" and body["created_at"] == failed["created_at"]
    assert body["analyzed_at"] >= failed["analyzed_at"]
    assert garment_client.get(body["image_url"]).content == original
    assert garment_client.get(body["cutout_url"]).headers["content-type"] == "image/png"
    assert storage.exists(body["cutout_path"])
    row = db_session.get(Garment, UUID(body["garment_id"]))
    assert row.attributes.raw_output["provider"] == "test-reanalysis"
    assert row.attributes.raw_output["result"]["success"] is True
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 1
    assert db_session.scalar(select(func.count()).select_from(GarmentAttribute)) == 1
    db_session.expunge_all()
    assert garment_client.get(path(body)).json() == body


@pytest.mark.parametrize("category", [None, "bottom"])
def test_only_explicitly_edited_fields_are_preserved_including_null_and_empty(
    garment_client, failed, db_session, category
):
    edits = {"color": "navy", "style_candidates": [], "subcategory": None, "thermal_level": None}
    if category:
        edits["category"] = category
    assert garment_client.patch(path(failed), json={**edits, "status": "laundry"}).status_code == 200
    for _ in range(2):
        result = garment_client.post(path(failed) + "/reanalyze").json()
        assert {k: result[k] for k in edits} == edits
        assert result["category"] == (category or "shoes")
        assert result["pattern"] == "solid" and result["status"] == "laundry"
        assert result["analysis_status"] == "done" and result["source"] == "user"
    row = db_session.get(Garment, UUID(failed["garment_id"]))
    assert row.attributes.user_edited_fields == sorted(edits)
    assert row.attributes.raw_output["result"]["attributes"]["color"] == "black"


@pytest.mark.parametrize("mode", ["exception", "invalid", "no-attributes"])
def test_failed_retry_preserves_values_and_images_then_can_recover(garment_client, failed, mode, db_session):
    first = garment_client.post(path(failed) + "/reanalyze").json()
    edited = garment_client.patch(path(first), json={"color": "navy", "status": "inactive"}).json()
    images = {key: garment_client.get(first[key]).content for key in ("image_url", "cutout_url")}

    class Broken:
        name = "broken"

        def extract(self, image_path, garment_id):
            # 모델이 입력/출력 파일을 손상시켜도 복사본에만 영향이 있어야 합니다.
            Path(image_path).write_bytes(b"broken")
            (Path(image_path).parent / "cutout.png").write_bytes(b"broken")
            if mode == "exception":
                raise RuntimeError("retry unavailable")
            if mode == "no-attributes":
                return VisionResult(success=True)
            return VisionResult(
                success=True,
                attributes=GarmentAttributes(garment_id=garment_id, category="top", thermal_level=99),
            )

    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(Broken(), 1)
    result = garment_client.post(path(first) + "/reanalyze")
    assert result.status_code == 200
    body = result.json()
    assert body["analysis_status"] == "failed" and body["error"]
    for key in (
        "category",
        "subcategory",
        "color",
        "pattern",
        "style_candidates",
        "thermal_level",
        "status",
        "source",
    ):
        assert body[key] == edited[key]
    for key, content in images.items():
        assert body[key] == edited[key] and garment_client.get(body[key]).content == content
    assert db_session.get(Garment, UUID(body["garment_id"])).analysis_status == "failed"
    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(NewVision(), 1)
    assert garment_client.post(path(first) + "/reanalyze").json()["analysis_status"] == "done"


def test_timeout_late_writer_cannot_overwrite_live_images(garment_client, failed):
    started, finish, stopped = Event(), Event(), Event()
    workspace = []
    original = garment_client.get(failed["image_url"]).content

    class Slow:
        name = "slow"

        def extract(self, image_path, garment_id):
            workspace.append(Path(image_path).parents[2])
            started.set()
            try:
                assert finish.wait(5)
                Path(image_path).write_bytes(b"late-corruption")
                return NewVision().extract(image_path, garment_id)
            finally:
                stopped.set()

    adapter = SafeVision(Slow(), 0.05)
    garment_client.app.dependency_overrides[get_vision] = lambda: adapter
    try:
        result = garment_client.post(path(failed) + "/reanalyze")
        assert started.is_set() and result.status_code == 200
        assert result.json()["analysis_status"] == "failed" and "timeout" in result.json()["error"]
        # 다음 재시도는 별도의 작업 공간과 어댑터로 성공합니다.
        garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(NewVision(), 1)
        current = garment_client.post(path(failed) + "/reanalyze").json()
        cutout = garment_client.get(current["cutout_url"]).content
    finally:
        finish.set()
        assert stopped.wait(5)
    deadline = time.monotonic() + 2
    while workspace[0].exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not workspace[0].exists()
    assert garment_client.get(current["image_url"]).content == original
    assert garment_client.get(current["cutout_url"]).content == cutout
    assert garment_client.get(path(failed)).json()["analysis_status"] == "done"


@pytest.mark.parametrize("failure", ["commit", "storage", "lost-commit-reply"])
def test_publishing_failure_never_damages_previous_images(
    garment_client, failed, db_session, storage, monkeypatch, failure
):
    before = garment_client.post(path(failed) + "/reanalyze").json()
    images = {key: garment_client.get(before[key]).content for key in ("image_url", "cutout_url")}
    commit = db_session.commit

    def fail_commit():
        if failure == "lost-commit-reply":
            commit()
        raise SQLAlchemyError("commit failed")

    def fail_save(*_):
        raise OSError("disk unavailable")

    with monkeypatch.context() as patch:
        if failure == "storage":
            patch.setattr(storage, "save", fail_save)
        else:
            patch.setattr(db_session, "commit", fail_commit)
        with pytest.raises((SQLAlchemyError, OSError)):
            garment_client.post(path(failed) + "/reanalyze")
    after = garment_client.get(path(before)).json()
    if failure != "lost-commit-reply":
        assert after == before
    for key, content in images.items():
        assert Path(storage.local_path(before[key].removeprefix("/media/"))).read_bytes() == content
    assert garment_client.get(after["cutout_url"]).status_code == 200
    # 이전/실패 버전도 같은 의류 폴더에 있으므로 삭제 시 함께 정리됩니다.
    assert garment_client.delete(path(before)).status_code == 204
    folder = Path(storage.local_path(before["image_url"].removeprefix("/media/"))).parent
    assert not folder.exists()


def test_owner_check_precedes_model_and_file_access(
    garment_client, failed, account_factory, auth_account, monkeypatch
):
    other = account_factory()

    def unexpected(*args, **kwargs):
        pytest.fail("unauthorized reanalysis must not call the model")

    monkeypatch.setattr(SafeVision, "extract_isolated", unexpected)
    target = path(failed) + "/reanalyze"
    denied = garment_client.post(target, headers=other["headers"])
    missing = garment_client.post(f"/api/v1/garments/{uuid4()}/reanalyze", headers=other["headers"])
    assert denied.status_code == 404 and denied.json() == missing.json()
    garment_client.headers.clear()
    assert garment_client.post(target).status_code == 401
    assert garment_client.post(target, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert (
        garment_client.post("/api/v1/garments/bad/reanalyze", headers=auth_account["headers"]).status_code
        == 422
    )


def test_missing_original_does_not_change_existing_record(garment_client, failed, storage, monkeypatch):
    Path(storage.local_path(failed["image_url"].removeprefix("/media/"))).unlink()

    def unexpected(*args, **kwargs):
        pytest.fail("missing original must not call the model")

    monkeypatch.setattr(SafeVision, "extract_isolated", unexpected)
    result = garment_client.post(path(failed) + "/reanalyze")
    assert result.status_code == 404 and result.json()["code"] == "GARMENT_IMAGE_NOT_FOUND"
    assert garment_client.get(path(failed)).json() == failed


@pytest.mark.parametrize("cutout", ["foreign", "invalid", "none"])
def test_missing_or_untrusted_cutout_keeps_previous_registered_image(garment_client, failed, cutout):
    before = garment_client.post(path(failed) + "/reanalyze").json()

    class Result:
        name = "cutout-test"

        def extract(self, image_path, garment_id):
            result = NewVision().extract(image_path, garment_id)
            if cutout == "foreign":
                result.attributes.cutout_path = f"{uuid4()}/{uuid4()}/cutout.png"
            elif cutout == "none":
                result.attributes.cutout_path = None
            else:
                (Path(image_path).parent / "cutout.png").write_bytes(b"invalid png")
            return result

    garment_client.app.dependency_overrides[get_vision] = lambda: SafeVision(Result(), 1)
    after = garment_client.post(path(failed) + "/reanalyze").json()
    assert after["analysis_status"] == "done" and after["cutout_url"] == before["cutout_url"]
