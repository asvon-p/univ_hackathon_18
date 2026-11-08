"""이미지 저장소 포트. MVP 는 LocalStorage, 사업화 시 S3Storage 를 추가합니다.

key 형식: "{user_id}/{garment_id}/original.jpg" (UUID 만 사용, 사용자 입력 금지)
"""

from typing import BinaryIO, Protocol


class StoragePort(Protocol):
    def save(self, key: str, data: bytes) -> str: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def local_path(self, key: str) -> str: ...
    def delete_prefix(self, prefix: str) -> None: ...


class InvalidStorageKey(ValueError):
    pass
