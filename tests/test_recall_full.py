"""Tests for the recall tool's `full` mode.

Per spec §8.2, `full` returns verbatim content at full token cost. This
is the agent's escape hatch when the stub isn't enough.
"""

from src.recall import recall
from src.store import hash_content


def test_recall_full_returns_verbatim(pager, large_code):
    """recall(id) with no query returns the exact original content."""
    r = pager.intercept("Read", {"file_path": "/repo/src/auth.go"}, large_code)
    # The first return was a stub. Re-stash to get a stable record.
    # Easier: stash directly.
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )

    result = recall(pager.store, record.id, mode="full")
    assert result.mode == "full"
    assert result.content == large_code
    assert not result.truncated
    assert not result.not_in_content


def test_recall_full_by_short_id(pager, large_code):
    """Recall works with a 6-char short ID."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id)
    assert result.content == large_code


def test_recall_full_by_full_hash(pager, large_code):
    """Recall works with the full 64-char hash."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.sha256)
    assert result.content == large_code


def test_recall_full_not_found(pager):
    """Recall with a bogus ID returns a not-found marker."""
    result = recall(pager.store, "deadbe")
    assert result.not_in_content
    assert "NOT_FOUND" in result.content


def test_recall_full_truncates_at_max_tokens(pager, large_code):
    """Recall caps output at max_tokens and flags truncation."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id, mode="full", max_tokens=50)
    assert result.truncated
    assert result.tokens <= 50
    assert "truncated" in result.content.lower()


def test_recall_full_invalid_mode(pager, large_code):
    """Recall rejects unknown modes."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id, mode="bogus")
    assert result.mode == "error"
    assert "INVALID_MODE" in result.content


def test_recall_full_unicode_preserved(pager):
    """Unicode content round-trips losslessly."""
    content = "Hello, 世界! 🎉\nLine two with ñ and € symbols.\n"
    record = pager.store.stash(
        content.encode("utf-8"),
        tool="Read",
        args={"file_path": "utf8.txt"},
    )
    result = recall(pager.store, record.id, mode="full")
    assert result.content == content
