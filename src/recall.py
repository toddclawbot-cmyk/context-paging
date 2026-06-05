"""
Recall tool implementation.

Per spec §8, the `recall` tool has three modes:
  - full     : verbatim content (one-time, full token cost)
  - extract  : query-extracted slice (small model, ≤200 token output)
  - lines    : specific line range (trivial cost)

The extractor prompt (per spec §8.3) is strict: quote verbatim, return
NOT_IN_CONTENT if the answer is not present, no speculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .store import StashStore


# Type for an optional LLM-backed extractor
LLMExtractor = Callable[[str, str], str]
_llm_extractor: LLMExtractor | None = None


def set_llm_extractor(fn: LLMExtractor | None) -> None:
    """Register an LLM-backed extractor at runtime. None = lines-only fallback."""
    global _llm_extractor
    _llm_extractor = fn


# --- Result type ---

@dataclass
class RecallResult:
    """Result of a recall operation."""
    content: str
    mode: str
    tokens: int
    truncated: bool = False
    not_in_content: bool = False

    def __str__(self) -> str:
        if self.not_in_content:
            return "NOT_IN_CONTENT"
        return self.content


# --- The recall tool ---

def recall(
    store: StashStore,
    stash_id: str,
    *,
    query: str | None = None,
    mode: str | None = None,
    range: tuple[int, int] | None = None,
    max_tokens: int = 8000,
) -> RecallResult:
    """
    Pull content back from a stash.

    Args:
        store: the StashStore to read from
        stash_id: full hash or 6+ char prefix
        query: optional question — drives extract mode
        mode: "full" | "extract" | "lines" — defaults by presence of query
        range: (start, end) line numbers for "lines" mode (1-indexed, inclusive)
        max_tokens: hard cap on returned tokens (default 8000)

    Returns:
        RecallResult with content, mode, tokens, and flags.
    """
    # Resolve record
    record = store.get_record(stash_id)
    if record is None:
        return RecallResult(
            content=f"NOT_FOUND: no stash with id {stash_id!r}",
            mode="error",
            tokens=0,
            not_in_content=True,
        )

    # Determine mode
    if mode is None:
        mode = "extract" if query else "full"
    if mode not in ("full", "extract", "lines"):
        return RecallResult(
            content=f"INVALID_MODE: {mode!r} (use full | extract | lines)",
            mode="error",
            tokens=0,
        )

    # Read raw content
    raw = store.read(stash_id)
    if raw is None:
        return RecallResult(
            content=f"NOT_FOUND: content for stash {stash_id!r} is missing",
            mode="error",
            tokens=0,
            not_in_content=True,
        )

    # Binary: base64-encode with a warning
    if record.get("binary"):
        import base64
        b64 = base64.b64encode(raw).decode("ascii")
        return RecallResult(
            content=f"⚠ BINARY CONTENT ({len(raw)} bytes, base64):\n{b64}",
            mode="full",
            tokens=len(b64) // 4,
        )

    text = raw.decode("utf-8", errors="replace")

    # Dispatch by mode
    if mode == "lines":
        return _recall_lines(text, range)
    if mode == "extract":
        return _recall_extract(text, query or "")
    # mode == "full"
    return _recall_full(text, max_tokens)


# --- Mode implementations ---

def _recall_full(text: str, max_tokens: int) -> RecallResult:
    """Return verbatim content, capped at max_tokens."""
    est_tokens = len(text) // 4
    if est_tokens <= max_tokens:
        return RecallResult(content=text, mode="full", tokens=est_tokens)
    # Truncate and flag
    cap_chars = max_tokens * 4
    truncated = text[:cap_chars]
    return RecallResult(
        content=truncated + f"\n\n[… truncated at {max_tokens} tokens; use mode='lines' for specific ranges]",
        mode="full",
        tokens=max_tokens,
        truncated=True,
    )


def _recall_lines(text: str, range: tuple[int, int] | None) -> RecallResult:
    """Return a specific line range."""
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return RecallResult(content="", mode="lines", tokens=0)
    if not range:
        # Default: first 50 lines
        range = (1, min(50, total))
    start, end = range
    # Clamp end to file size, but if start is past EOF, return empty
    end = min(total, end)
    if start > total:
        # Range entirely beyond file — silent empty result
        return RecallResult(content="", mode="lines", tokens=0)
    start = max(1, start)
    if start > end:
        return RecallResult(content="", mode="lines", tokens=0)
    selected = "\n".join(lines[start - 1: end])
    return RecallResult(
        content=selected,
        mode="lines",
        tokens=len(selected) // 4,
    )


EXTRACTOR_SYSTEM = (
    "You are a content extractor. Given source content and a query, "
    "return ONLY the portion of the content that directly answers the query. "
    "Quote verbatim where possible. If the answer is not in the content, "
    "respond exactly: NOT_IN_CONTENT. Do not speculate. Maximum 200 tokens."
)


def _recall_extract(text: str, query: str) -> RecallResult:
    """
    Return a query-extracted slice.

    Strategy:
      1. If no LLM extractor registered: fall back to a smart lines-based
         heuristic that returns the first ~50 lines + a note.
      2. If LLM extractor registered: dispatch to it.
    """
    if not query.strip():
        return RecallResult(
            content="MISSING_QUERY: extract mode requires a query",
            mode="error",
            tokens=0,
        )

    if _llm_extractor is None:
        # Heuristic fallback: keyword-based line selection
        return _heuristic_extract(text, query)

    try:
        extracted = _llm_extractor(text, query)
    except Exception as e:
        return RecallResult(
            content=f"EXTRACTOR_ERROR: {e}",
            mode="error",
            tokens=0,
        )

    if extracted.strip() == "NOT_IN_CONTENT":
        return RecallResult(
            content="NOT_IN_CONTENT",
            mode="extract",
            tokens=0,
            not_in_content=True,
        )

    return RecallResult(
        content=extracted,
        mode="extract",
        tokens=len(extracted) // 4,
    )


def _heuristic_extract(text: str, query: str) -> RecallResult:
    """
    No-LLM fallback for extract mode: keyword-based line selection.

    Returns up to 30 lines that contain any of the query's keywords, plus
    a note that an LLM extractor wasn't registered. This is intentionally
    conservative — better to return too much than miss the answer.
    """
    import re

    keywords = [k.lower() for k in re.findall(r"\w+", query) if len(k) >= 3]
    if not keywords:
        # Treat the whole query as one keyword
        keywords = [query.lower()]

    lines = text.splitlines()
    scored: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines, 1):
        lower = line.lower()
        score = sum(1 for kw in keywords if kw in lower)
        if score > 0:
            scored.append((score, i, line))

    if not scored:
        return RecallResult(
            content="NOT_IN_CONTENT (heuristic; no LLM extractor registered)",
            mode="extract",
            tokens=0,
            not_in_content=True,
        )

    # Sort by score desc, then line number asc
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = scored[:30]
    # Build output with line numbers
    out_lines = [f"L{i:>4}: {line}" for _, i, line in top]
    content = "\n".join(out_lines)
    content += "\n\n(heuristic extract — no LLM extractor registered)"
    return RecallResult(
        content=content,
        mode="extract",
        tokens=len(content) // 4,
    )
