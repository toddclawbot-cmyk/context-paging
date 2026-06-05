"""Auth tests."""
import pytest
from myshop.auth import hash_password, verify_password, hash_refresh_token


def test_hash_and_verify_roundtrip():
    h = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", h)
    assert not verify_password("wrong", h)


def test_verify_returns_false_for_malformed_hash():
    assert verify_password("anything", "not-a-real-hash") is False


def test_hash_refresh_is_deterministic_and_short():
    h = hash_refresh_token("token-abc-123")
    assert h == hash_refresh_token("token-abc-123")
    assert len(h) == 64  # sha256 hex
