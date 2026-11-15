from concurrent.futures import ThreadPoolExecutor, TimeoutError
from io import BytesIO
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.services import garment as garment_module
from backend.app.services.garment import GarmentService
from backend.app.storage.local import LocalStorage
from backend.tests.e2e.test_reanalysis import register, upload


@pytest.mark.parametrize("live_api", ["local"], indirect=True)
def test_live_cached_thumbnails_ownership_and_parallel_requests(live_api):
    client, _, runtime = live_api
    _, headers = register(client)
    _, other = register(client)
    item = upload(client, headers)
    path = f"/api/v1/garments/{item['garment_id']}/image"
    original = client.get(path, params={"kind": "original"}, headers=headers).content
    for p in runtime["storage_dir"].rglob("*.webp"):
        p.unlink()  # Only this fixture's isolated directory: emulate existing pre-thumbnail data.
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = list(
            pool.map(lambda _: client.get(path, params={"kind": "thumbnail"}, headers=headers), range(4))
        )
    assert all(r.status_code == 200 and r.headers["content-type"] == "image/webp" for r in images)
    assert all(r.content == images[0].content for r in images)
    assert len(list(runtime["storage_dir"].rglob("*.webp"))) == 1
    with Image.open(BytesIO(images[0].content)) as im:
        assert max(im.size) <= 384
    assert client.get(path, params={"kind": "thumbnail"}, headers=other).status_code == 404
    assert client.get(path, params={"kind": "thumbnail"}).status_code == 401
    assert client.get(path, params={"kind": "original"}, headers=headers).content == original


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
@pytest.mark.parametrize("withdrawal", [False, True])
def test_thumbnail_generation_serializes_with_deletion(live_api, monkeypatch, withdrawal):
    client, _, runtime = live_api
    user_id, headers = register(client)
    item = upload(client, headers)
    gid = UUID(item["garment_id"])
    for p in runtime["storage_dir"].rglob("*.webp"):
        p.unlink()
    storage = LocalStorage(runtime["storage_dir"])
    engine = create_engine(runtime["database_url"])
    started, release, deleting = Event(), Event(), Event()
    encode = garment_module.create_thumbnail

    def slow(storage, source):
        started.set()
        assert release.wait(10)
        return encode(storage, source)

    monkeypatch.setattr(garment_module, "create_thumbnail", slow)

    def generate():
        with Session(engine) as db:
            result = GarmentService(db, storage).thumbnail(user_id, gid)
            db.commit()
            return result

    def delete():
        deleting.set()
        path = "/api/v1/auth/me" if withdrawal else f"/api/v1/garments/{gid}"
        return client.delete(path, headers=headers)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(generate)
            try:
                assert started.wait(5)
                deletion = pool.submit(delete)
                assert deleting.wait(2)
                with pytest.raises(TimeoutError):
                    deletion.result(timeout=0.2)
            finally:
                release.set()
            pending.result(timeout=5)
            assert deletion.result(timeout=5).status_code == 204
        assert not Path(storage.local_path(f"{user_id}/{gid}")).exists()
        result = client.get(f"/api/v1/garments/{gid}/image?kind=thumbnail", headers=headers)
        assert result.status_code == (401 if withdrawal else 404)
        assert not Path(storage.local_path(f"{user_id}/{gid}")).exists()
    finally:
        release.set()
        engine.dispose()
