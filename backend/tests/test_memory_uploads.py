from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from shared.memory_uploads import MemoryUploadMiddleware


def test_large_multipart_photo_does_not_roll_over_to_disk():
    app = FastAPI()
    app.add_middleware(MemoryUploadMiddleware)

    @app.post("/photo")
    async def photo(file: UploadFile):
        return {"rolled": file.file._rolled, "size": len(await file.read())}

    with TestClient(app) as client:
        response = client.post(
            "/photo", files={"file": ("photo.jpg", b"x" * (2 * 1024 * 1024), "image/jpeg")}
        )
    assert response.json() == {"rolled": False, "size": 2 * 1024 * 1024}
