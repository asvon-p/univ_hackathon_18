import pytest

from backend.app.storage.base import InvalidStorageKey
from backend.app.storage.local import LocalStorage


def test_save_open_delete(tmp_path):
    s = LocalStorage(tmp_path)
    s.save("u1/g1/original.jpg", b"abc")
    assert s.exists("u1/g1/original.jpg")
    with s.open("u1/g1/original.jpg") as f:
        assert f.read() == b"abc"
    s.delete_prefix("u1/g1")
    assert not s.exists("u1/g1/original.jpg")


@pytest.mark.parametrize("key", ["../evil", "/etc/passwd", "u1/../../x", "C:/win", "a\\b", ""])
def test_rejects_path_traversal(tmp_path, key):
    with pytest.raises(InvalidStorageKey):
        LocalStorage(tmp_path).save(key, b"x")


@pytest.mark.parametrize("prefix", [".", "./", "/", "u/.."])
def test_delete_cannot_remove_storage_root(tmp_path, prefix):
    storage = LocalStorage(tmp_path)
    storage.save("keep/original.jpg", b"preserve")
    with pytest.raises(InvalidStorageKey):
        storage.delete_prefix(prefix)
    assert storage.exists("keep/original.jpg")
