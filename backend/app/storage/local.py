import shutil
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from backend.app.core.config import get_settings
from backend.app.storage.base import InvalidStorageKey


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = PurePosixPath(key)
        if not key or p.is_absolute() or "\\" in key or ".." in p.parts or ":" in key:
            raise InvalidStorageKey(key)
        full = (self.root / p).resolve()
        if self.root not in full.parents and full != self.root:
            raise InvalidStorageKey(key)
        return full

    def save(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return key

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def local_path(self, key: str) -> str:
        return str(self._path(key))

    def delete_prefix(self, prefix: str) -> None:
        target = self._path(prefix.rstrip("/"))
        if target == self.root:
            raise InvalidStorageKey(prefix)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()


@lru_cache
def get_storage() -> LocalStorage:
    return LocalStorage(get_settings().STORAGE_DIR)
