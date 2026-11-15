import os

import pytest

from scripts import openai_credentials as credentials
from scripts import recommendation_runtime as runtime


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_encrypted_key_roundtrip_and_invalid_update_preserves_existing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    path = tmp_path / "key.dpapi"
    secret = "test-only-fake-key-1234567890"
    credentials.save_key(secret, path)
    ciphertext = path.read_bytes()
    assert secret.encode() not in ciphertext and credentials.load_key(path) == secret
    with pytest.raises(ValueError):
        credentials.save_key(" ", path)
    assert path.read_bytes() == ciphertext
    path.write_bytes(b"invalid ciphertext")
    with pytest.raises(RuntimeError):
        credentials.load_key(path)


def test_runtime_auto_and_frontend_key_isolation(monkeypatch):
    monkeypatch.setattr(runtime, "load_key", lambda: "test-secret")
    env = runtime.recommendation_environment()
    assert env["RECOMMENDER_PROVIDER"] == "openai" and env["OPENAI_MODEL"] == "gpt-6-luna"
    assert env["OPENAI_API_KEY"] == "test-secret"
    frontend = runtime.frontend_environment(env)
    assert frontend["OPENAI_API_KEY"] == frontend["LLM_API_KEY"] == ""
    assert env["OPENAI_API_KEY"] == "test-secret"


def test_explicit_local_never_reads_credentials(monkeypatch):
    def forbidden():
        raise AssertionError("must not read credentials")

    monkeypatch.setattr(runtime, "load_key", forbidden)
    assert runtime.recommendation_environment("local")["RECOMMENDER_PROVIDER"] == "local"


@pytest.mark.parametrize("broken", [False, True])
def test_missing_or_broken_credentials_keep_demo_available(monkeypatch, broken):
    def unavailable():
        if broken:
            raise RuntimeError("DPAPI failure")
        return ""

    monkeypatch.setattr(runtime, "load_key", unavailable)
    assert runtime.recommendation_environment()["RECOMMENDER_PROVIDER"] == "local"
    explicit = runtime.recommendation_environment("openai")
    assert explicit["RECOMMENDER_PROVIDER"] == "openai" and not explicit["OPENAI_API_KEY"]
