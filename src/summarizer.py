"""
Summary generation.

Per spec §7.4, the 1-line summary is the only freeform field and is the
riskiest one. We use a templated heuristic-first approach:

  1. Try cheap structural heuristics (no LLM) for known shapes.
  2. Fall back to first-non-blank-line + size + extension.
  3. If `use_llm=True`, dispatch to a callable (so the same module can run
     cloud or local models without rewriting).

The MVP ships with heuristic-only summaries. LLM-backed summaries slot in
when the user wires up a model.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .extractor import detect_language, extract_markdown, extract_code


# Heuristic signatures --------------------------------------------------------------

_DOCSTRING_RE = re.compile(r'^\s*(?:"""|\'\'\')(.+?)(?:"""|\'\'\')', re.DOTALL)
_COMMENT_LINE_RE = re.compile(r"^\s*(?://|#|--)\s*(.+?)\s*$")


def _extract_first_docstring_or_comment(content: str, language: str) -> str | None:
    """Pull the first module-level docstring or comment block."""
    # Python docstring
    if language == "python":
        m = _DOCSTRING_RE.search(content)
        if m:
            first = m.group(1).strip().split("\n", 1)[0].strip()
            if first and len(first) > 5:
                return first[:120]
    # First significant comment line
    for line in content.splitlines()[:30]:
        m = _COMMENT_LINE_RE.match(line)
        if m:
            text = m.group(1).strip()
            if len(text) > 8 and not text.startswith("!"):
                return text[:120]
    return None


def _summary_from_toc(structure: dict[str, Any]) -> str | None:
    """For markdown, summarize from the first heading."""
    toc = structure.get("toc", [])
    if toc and toc[0] and not toc[0].startswith("…"):
        return f"Doc with {structure.get('headings', 0)} headings; top: {toc[0]}"
    return None


def _summary_from_exports(structure: dict[str, Any]) -> str | None:
    """For code, summarize from the first exports."""
    exports = [e for e in structure.get("exports", []) if not e.startswith("…")]
    imports = [i for i in structure.get("imports", []) if not i.startswith("…")]
    parts = []
    if exports:
        first_three = ", ".join(exports[:3])
        parts.append(f"defines {first_three}")
    if imports:
        parts.append(f"uses {', '.join(imports[:3])}")
    if parts:
        return "; ".join(parts)
    return None


def _summary_from_grep(structure: dict[str, Any]) -> str | None:
    pat = structure.get("pattern", "")
    count = structure.get("match_count", 0)
    files = structure.get("file_count", 0)
    if pat and count:
        return f"Grep for {pat!r}: {count} matches in {files} file(s)"
    return None


def _summary_from_bash(structure: dict[str, Any]) -> str | None:
    cmd = structure.get("cmd", "")
    exit_code = structure.get("exit_code", 0)
    head = structure.get("stdout_head", "").strip()
    if not cmd:
        return None
    suffix = ""
    if exit_code != 0:
        suffix = f" (exit {exit_code})"
    if head:
        return f"`{cmd[:40]}`{suffix}: {head[:60]}"
    return f"`{cmd[:40]}`{suffix}"


def _summary_fallback(content: str, path: str) -> str:
    """Last-resort: first non-blank line + size."""
    first = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            first = stripped[:80]
            break
    if path:
        ext = Path(path).suffix or "file"
        if first:
            return f"{ext} file: {first}"
        return f"{ext} file, {len(content)} bytes"
    return first or f"{len(content)} bytes of content"


# --- Main entry point --------------------------------------------------------

# Type for an optional LLM-backed summarizer
LLMSummarizer = Callable[[str, str, dict[str, Any]], str]
_llm_summarizer: LLMSummarizer | None = None


def set_llm_summarizer(fn: LLMSummarizer | None) -> None:
    """Register an LLM-backed summarizer at runtime. None = heuristic only."""
    global _llm_summarizer
    _llm_summarizer = fn


def summarize(
    content: str,
    *,
    tool: str,
    structure: dict[str, Any] | None = None,
    path: str = "",
    use_llm: bool = False,
) -> str:
    """
    Generate a one-line summary of a tool result.

    Strategy:
      1. Try cheap structural heuristic based on tool+structure
      2. Try docstring/comment extraction
      3. Try LLM summarizer if use_llm=True
      4. Fall back to first non-blank line

    The output is capped at ~120 chars / ~30 tokens.
    """
    structure = structure or {}

    # Tool-specific heuristics
    if tool in ("Read", "read"):
        # Markdown doc
        if structure.get("language") == "markdown":
            s = _summary_from_toc(structure)
            if s:
                return _clip(s)
        # Code file
        if structure.get("language") and structure.get("language") != "text":
            s = _summary_from_exports(structure)
            if s:
                return _clip(s)
        # Try docstring/comment
        lang = detect_language(path) or ""
        ds = _extract_first_docstring_or_comment(content, lang)
        if ds:
            return _clip(ds)
    elif tool in ("Grep", "grep"):
        s = _summary_from_grep(structure)
        if s:
            return _clip(s)
    elif tool in ("Bash", "bash", "BashOutput"):
        s = _summary_from_bash(structure)
        if s:
            return _clip(s)

    # LLM fallback
    if use_llm and _llm_summarizer is not None:
        try:
            llm_summary = _llm_summarizer(content, tool, structure)
            if llm_summary:
                return _clip(llm_summary)
        except Exception:
            pass  # fall through to heuristic

    # Last-resort fallback
    return _clip(_summary_fallback(content, path))


def _clip(s: str, max_len: int = 120) -> str:
    """Cap a summary at max_len characters."""
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
