"""Tests for the recall tool's `extract` and `lines` modes.

Per spec §8.3, extract mode dispatches to a small extractor model that
returns a query-relevant slice (≤200 tokens) or `NOT_IN_CONTENT`. The MVP
ships with a heuristic fallback so the system is testable without an
LLM.
"""

import pytest

from src.recall import (
    recall,
    set_llm_extractor,
    _heuristic_extract,
)


@pytest.fixture(autouse=True)
def _reset_llm_extractor():
    """Make sure no LLM extractor leaks between tests."""
    set_llm_extractor(None)
    yield
    set_llm_extractor(None)


def test_recall_extract_heuristic_finds_matching_lines(pager, large_code):
    """Heuristic extract selects lines with query keywords."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id, query="verify_token expired")
    assert result.mode == "extract"
    assert "verify_token" in result.content
    assert not result.not_in_content


def test_recall_extract_heuristic_returns_not_in_content(pager, large_code):
    """Heuristic extract returns NOT_IN_CONTENT when no keywords match."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id, query="xyzzy frobnicate")
    assert result.not_in_content


def test_recall_extract_requires_query(pager, large_code):
    """Extract mode without a query returns an error."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id, mode="extract")
    assert result.mode == "error"
    assert "MISSING_QUERY" in result.content


def test_recall_extract_with_llm_extractor(pager, large_code):
    """When an LLM extractor is registered, it is used."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )

    def fake_extractor(content, query):
        # Pretend the LLM extracted something specific
        return "verify_token returns (None, 'expired') on ExpiredSignatureError"

    set_llm_extractor(fake_extractor)
    result = recall(pager.store, record.id, query="what does verify_token return?")
    assert result.mode == "extract"
    assert "expired" in result.content
    assert not result.not_in_content


def test_recall_extract_llm_returns_not_in_content(pager, large_code):
    """An LLM that returns NOT_IN_CONTENT gets surfaced as a flag."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )

    def fake_extractor(content, query):
        return "NOT_IN_CONTENT"

    set_llm_extractor(fake_extractor)
    result = recall(pager.store, record.id, query="what is the meaning of life?")
    assert result.not_in_content


def test_recall_extract_llm_error(pager, large_code):
    """An LLM that raises gets surfaced as a clean error, not a crash."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )

    def broken_extractor(content, query):
        raise RuntimeError("model unavailable")

    set_llm_extractor(broken_extractor)
    result = recall(pager.store, record.id, query="anything")
    assert result.mode == "error"
    assert "EXTRACTOR_ERROR" in result.content


def test_recall_lines_specific_range(pager, large_code):
    """Lines mode returns a specific line range."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    # Recall lines 5-10 (1-indexed, inclusive)
    result = recall(pager.store, record.id, mode="lines", range=(5, 10))
    assert result.mode == "lines"
    lines = result.content.splitlines()
    assert 1 <= len(lines) <= 6
    # No truncation flag, no not-in-content
    assert not result.truncated
    assert not result.not_in_content


def test_recall_lines_default_range(pager, large_code):
    """Lines mode with no range defaults to first 50 lines."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id, mode="lines")
    assert result.mode == "lines"
    lines = result.content.splitlines()
    assert len(lines) <= 50


def test_recall_lines_inverted_range(pager, large_code):
    """Lines mode with start>end (both within file) returns empty gracefully."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id, mode="lines", range=(100, 50))
    # We silently clamp/empty rather than erroring — same UX as out-of-bounds
    assert result.mode == "lines"


def test_recall_lines_out_of_bounds(pager, large_code):
    """Lines mode with range beyond file end is clamped silently."""
    record = pager.store.stash(
        large_code.encode("utf-8"),
        tool="Read",
        args={"file_path": "/repo/src/auth.py"},
    )
    result = recall(pager.store, record.id, mode="lines", range=(1000, 2000))
    # Doesn't error — silently clamps
    assert result.mode == "lines"
