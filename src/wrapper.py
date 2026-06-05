"""
Tool-result interceptor (the wrapper).

Per spec §4.1 and §5, the wrapper sits between the agent loop and the
underlying tool implementations. For each tool result:
  1. Token-count the result
  2. If below threshold: pass through unchanged
  3. If above threshold: extract structure, write to stash store, return stub

This module exposes the core logic as a function (`intercept_tool_result`)
that any harness can call, plus a convenience class for testing and CLI use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .extractor import extract
from .recall import recall, RecallResult, set_llm_extractor
from .store import StashStore, is_sensitive_path, estimate_tokens
from .stub import format_stub
from .summarizer import summarize, set_llm_summarizer


# --- Configuration ----------------------------------------------------------

DEFAULT_THRESHOLD = 500   # tokens
DEFAULT_STUB_DEPTH = "outline"


@dataclass
class InterceptorConfig:
    """Tunables for the wrapper."""
    threshold_tokens: int = DEFAULT_THRESHOLD
    default_stub_depth: str = DEFAULT_STUB_DEPTH
    always_stash_tools: tuple[str, ...] = ()    # e.g. ("BashOutput",)
    never_stash_tools: tuple[str, ...] = ()     # e.g. ("recall",)
    use_llm_summarizer: bool = False
    redact_secrets: bool = True


# --- The interceptor function -----------------------------------------------

def intercept_tool_result(
    content: str | bytes,
    *,
    tool: str,
    args: dict[str, Any] | None = None,
    store: StashStore,
    config: InterceptorConfig | None = None,
) -> str:
    """
    Process a single tool result. Return either the original content (if
    small enough) or a stub (if stashed).

    Args:
        content: the raw tool result (string or bytes)
        tool: the tool name (e.g. "Read", "Bash", "Grep")
        args: the args the tool was called with
        store: the StashStore to write to
        config: tunables (uses defaults if None)

    Returns:
        The content the agent should see (verbatim or stub).
    """
    config = config or InterceptorConfig()

    # Normalize content
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
        text = content
    else:
        content_bytes = content
        text = content.decode("utf-8", errors="replace")

    # Size check
    token_est = estimate_tokens(text) if text else len(content_bytes) // 4

    # Tool-specific overrides
    if tool in config.never_stash_tools:
        return text
    if tool in config.always_stash_tools:
        pass  # skip the threshold check
    elif token_est < config.threshold_tokens:
        return text  # pass-through, no stash

    # Path sensitivity
    file_path = (args or {}).get("file_path", "")
    sensitive = is_sensitive_path(file_path) if file_path else False

    # Extract structure
    structure = extract(text, tool=tool, args=args, path=file_path)

    # Generate summary
    summary = summarize(
        text,
        tool=tool,
        structure=structure,
        path=file_path,
        use_llm=config.use_llm_summarizer,
    )

    # Write to store
    record = store.stash(
        content_bytes,
        tool=tool,
        args=args or {},
        structure=structure,
        summary=summary,
        stub_depth=config.default_stub_depth,
        sensitive=sensitive,
    )

    # Format and return stub
    return format_stub(record.to_dict(), depth=config.default_stub_depth)


# --- High-level convenience API ---------------------------------------------

class ContextPager:
    """
    Convenience wrapper combining the store, recall tool, and interceptor.

    Usage:
        pager = ContextPager("/path/to/stash/root")
        # 1. Interceptor
        out = pager.intercept("Read", {"file_path": "auth.go"}, "def foo(): ...", raw_content)
        # 2. Recall tool
        result = pager.recall("abc123", query="what does foo return?")
    """

    def __init__(
        self,
        store_root: str | os.PathLike[str],
        config: InterceptorConfig | None = None,
    ):
        self.store = StashStore(store_root)
        self.config = config or InterceptorConfig()

    def intercept(
        self,
        tool: str,
        args: dict[str, Any],
        text: str,
    ) -> str:
        """Shortcut for intercept_tool_result using this pager's store/config."""
        return intercept_tool_result(
            text,
            tool=tool,
            args=args,
            store=self.store,
            config=self.config,
        )

    def recall(
        self,
        stash_id: str,
        *,
        query: str | None = None,
        mode: str | None = None,
        range: tuple[int, int] | None = None,
        max_tokens: int = 8000,
    ) -> RecallResult:
        """Shortcut for recall() using this pager's store."""
        return recall(
            self.store,
            stash_id,
            query=query,
            mode=mode,
            range=range,
            max_tokens=max_tokens,
        )

    def set_llm_summarizer(self, fn: Callable[[str, str, dict[str, Any]], str] | None) -> None:
        set_llm_summarizer(fn)
        self.config.use_llm_summarizer = fn is not None

    def set_llm_extractor(self, fn: Callable[[str, str], str] | None) -> None:
        set_llm_extractor(fn)
