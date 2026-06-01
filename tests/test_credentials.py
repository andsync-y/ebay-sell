import pytest

from src.auth.credentials import EncryptedFileStore
from src.models import Credential


def test_set_get_roundtrip():
    store = EncryptedFileStore()
    cred = Credential("alice", "rakuten", "app_id", {"app_id": "abc123"})
    store.set("alice", "rakuten", cred)
    got = store.get("alice", "rakuten")
    assert got is not None
    assert got.data["app_id"] == "abc123"


def test_user_isolation():
    store = EncryptedFileStore()
    store.set("alice", "ebay", Credential("alice", "ebay", "oauth_token", {"access_token": "A"}))
    store.set("bob", "ebay", Credential("bob", "ebay", "oauth_token", {"access_token": "B"}))
    assert store.get("alice", "ebay").data["access_token"] == "A"
    assert store.get("bob", "ebay").data["access_token"] == "B"
    # Bob has nothing of Alice's.
    assert "rakuten" not in store.list_services("bob")


def test_secrets_not_plaintext_on_disk(tmp_path):
    from pathlib import Path

    store = EncryptedFileStore()
    store.set("carol", "ebay", Credential("carol", "ebay", "oauth_token",
                                          {"access_token": "SUPER-SECRET-TOKEN"}))
    # Find carol's credential file and ensure the secret isn't readable.
    got = store.get("carol", "ebay")
    assert got.data["access_token"] == "SUPER-SECRET-TOKEN"
    path = store._path("carol")
    raw = Path(path).read_bytes()
    assert b"SUPER-SECRET-TOKEN" not in raw


def test_delete():
    store = EncryptedFileStore()
    store.set("dave", "rakuten", Credential("dave", "rakuten", "app_id", {"app_id": "x"}))
    store.delete("dave", "rakuten")
    assert store.get("dave", "rakuten") is None
