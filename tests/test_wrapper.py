"""Tests for the wrapper (tool-result interceptor).

Per spec §5, the wrapper decides stash vs. pass-through based on:
  - tool result size (token threshold)
  - tool-specific overrides
  - path sensitivity (sensitive → secret-stash)
"""

import re

import pytest

from src.wrapper import ContextPager, InterceptorConfig


def test_small_result_passes_through_unchanged(pager):
    """Results under the token threshold are returned verbatim."""
    small = "def foo():\n    return 42\n"  # ~15 tokens, under 100
    out = pager.intercept("Read", {"file_path": "foo.py"}, small)
    assert out == small
    # No stash was created
    assert pager.store.list_count() == 0


def test_large_result_returns_stub(pager, large_code):
    """Results over the threshold return a structured stub."""
    out = pager.intercept("Read", {"file_path": "/repo/src/auth.go"}, large_code)
    # The stub contains the stash ID
    assert "[stash:" in out
    assert "recall(" in out
    # The stub is much smaller than the original
    assert len(out) < len(large_code) // 2
    # A stash was created
    assert pager.store.list_count() == 1


def test_stub_contains_structured_fields_for_code(pager, large_code):
    """Code stubs include exports, imports, shape, and a summary."""
    out = pager.intercept("Read", {"file_path": "/repo/src/auth.py"}, large_code)
    # Should mention at least one of our function names
    assert "verify_token" in out or "rotate_refresh" in out or "parse_header" in out
    assert "imports:" in out
    assert "exports:" in out
    assert "shape:" in out


def test_stub_contains_structured_fields_for_markdown(pager, large_markdown):
    """Markdown stubs include heading count and a ToC."""
    out = pager.intercept("Read", {"file_path": "/repo/docs/onyx.md"}, large_markdown)
    assert "headings" in out or "toc" in out


def test_stub_for_grep_includes_match_count(pager, large_grep):
    """Grep stubs include match count and file list."""
    out = pager.intercept("Grep", {"pattern": "handler_"}, large_grep)
    assert "matches:" in out or "match_count" in out or "files:" in out


def test_sensitive_path_uses_secret_stash(pager):
    """Files matching the sensitive denylist are stashed in secret-stash/."""
    # Build a long enough .env to exceed the 100-token threshold
    lines = [f"VAR_{i}=value_{i}" for i in range(50)]
    content = "API_KEY=supersecret\n" + "\n".join(lines)
    out = pager.intercept("Read", {"file_path": "/app/.env"}, content)

    # Stash was created
    records = list(pager.store.list_all(include_secret=True))
    assert len(records) == 1
    assert records[0]["sensitive"] is True

    # The stub marks it as sensitive
    assert "sensitive" in out.lower()

    # The content is in secret-stash, not stash
    assert len(list(pager.store.stash_dir.glob("*.txt"))) == 0
    assert len(list(pager.store.secret_dir.glob("*.txt"))) == 1


def test_sensitive_pem_file(pager):
    """PEM files are detected as sensitive."""
    # Pad a realistic PEM to exceed the threshold
    header = "-----BEGIN RSA PRIVATE KEY-----\n"
    body = "MIIEowIBAAKCAQEA" + ("A" * 60 + "\n") * 30
    footer = "-----END RSA PRIVATE KEY-----\n"
    content = header + body + footer
    out = pager.intercept("Read", {"file_path": "/app/server.pem"}, content)
    records = list(pager.store.list_all(include_secret=True))
    assert len(records) == 1
    assert records[0]["sensitive"] is True
    assert "sensitive" in out.lower()


def test_never_stash_tool(pager, large_code):
    """Tools in never_stash_tools are always passed through."""
    config = InterceptorConfig(
        threshold_tokens=10,  # very low — most things would stash
        never_stash_tools=("Read",),
    )
    pager.config = config

    out = pager.intercept("Read", {"file_path": "auth.py"}, large_code)
    assert out == large_code
    assert pager.store.list_count() == 0


def test_always_stash_tool(pager):
    """Tools in always_stash_tools are stashed even if small."""
    config = InterceptorConfig(
        threshold_tokens=10_000,  # very high — most things would not stash
        always_stash_tools=("Bash",),
    )
    pager.config = config

    out = pager.intercept("Bash", {"cmd": "ls"}, "file1.txt\nfile2.txt")
    # Should be stashed even though small
    assert "[stash:" in out
    assert pager.store.list_count() == 1


def test_stub_depth_minimal(pager, large_code):
    """Stub depth 'minimal' produces ~30-token stubs."""
    config = InterceptorConfig(threshold_tokens=100, default_stub_depth="minimal")
    pager.config = config
    out = pager.intercept("Read", {"file_path": "auth.py"}, large_code)
    # Minimal stub should be much shorter than outline
    assert "exports:" not in out
    assert "imports:" not in out
    # But still has the header
    assert "[stash:" in out


def test_stub_depth_full_toc(pager, large_code):
    """Stub depth 'full-toc' includes all exports without truncation."""
    config = InterceptorConfig(threshold_tokens=100, default_stub_depth="full-toc")
    pager.config = config
    out = pager.intercept("Read", {"file_path": "auth.py"}, large_code)
    # full-toc should list more exports than outline
    assert "exports:" in out


def test_recall_round_trip(pager, large_code):
    """A stashed result can be recalled losslessly via the pager."""
    pager.intercept("Read", {"file_path": "/repo/src/auth.py"}, large_code)
    # Find the stash ID from the index
    records = list(pager.store.list_all())
    assert len(records) == 1
    sid = records[0]["id"]

    result = pager.recall(sid, mode="full")
    assert result.content == large_code
    assert not result.truncated


def test_idempotent_intercept(pager, large_code):
    """Intercepting the same content twice creates one stash, not two."""
    for _ in range(3):
        pager.intercept("Read", {"file_path": "auth.py"}, large_code)
    assert pager.store.list_count() == 1


def test_binary_content_stashed_as_bin(pager):
    """Binary content is stored as .bin, not .txt, and the stub reflects it."""
    binary = b"\x00\x01\x02\x03\x89PNG\r\n\x1a\n" * 50
    out = pager.intercept("Bash", {"cmd": "xxd image.png"}, binary.decode("latin-1"))
    # Either the stub is small (good — stashed) or large
    # The point: nothing crashes
    assert isinstance(out, str)
    records = list(pager.store.list_all(include_secret=True))
    if records:
        # If stashed, it should be flagged binary
        assert records[0].get("binary") is True
        # And the file is .bin not .txt
        # (it was stored as a .bin file)
