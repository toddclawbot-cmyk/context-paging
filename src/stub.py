"""
Stub formatter.

Per spec §7, stubs are structured and routing-grade, not authoritative.
The agent uses the stub to decide whether to recall, not to answer.

Three depths:
  - minimal    (~30 tokens)   one-shot reads, throwaway grep, transient API
  - outline    (~80 tokens)   default for code/config files
  - full-toc   (~250 tokens)  files the agent expects to revisit
"""

from __future__ import annotations

from typing import Any


def format_stub(
    record: dict[str, Any],
    *,
    depth: str | None = None,
) -> str:
    """
    Format a structured stub for a stashed tool result.

    Args:
        record: a stash metadata dict (from StashStore.get_record)
        depth: stub depth — minimal | outline | full-toc.
               Defaults to the record's `stub_depth` field.

    The output is a plain-text stub suitable for injection into context.
    """
    depth = depth or record.get("stub_depth", "outline")
    if depth not in ("minimal", "outline", "full-toc"):
        depth = "outline"

    short_id = record.get("id", "??????")
    tool = record.get("tool", "Tool")
    args = record.get("args", {}) or {}
    size_tok = record.get("size_tokens", 0)
    summary = record.get("summary", "")

    # Header line: [stash:id] Tool(args) — N tok
    args_str = _format_args(args)
    header = f"[stash:{short_id}] {tool}({args_str}) — {size_tok} tok"

    lines = [header]
    structure = record.get("structure", {}) or {}

    # Binary content gets a special-case stub
    if record.get("binary"):
        lines.append(f"  binary content, {record.get('size_bytes', 0)} bytes")
        lines.append(f"  summary: {summary or '(none)'}")
        lines.append(f"  recall({short_id}) for base64")
        return "\n".join(lines)

    if depth == "minimal":
        # Header + one-line summary
        if summary:
            lines.append(f"  summary: {summary}")
        if record.get("sensitive"):
            lines.append("  ⚠ sensitive")
        lines.append(f"  recall({short_id}) for content")
        return "\n".join(lines)

    if depth == "outline":
        # Code/markdown structure + summary
        if "exports" in structure and structure["exports"]:
            exports = structure["exports"]
            lines.append(f"  exports: {_join_truncated(exports)}")
        if "imports" in structure and structure["imports"]:
            imports = structure["imports"]
            lines.append(f"  imports: {_join_truncated(imports)}")
        if "headings" in structure:
            headings = structure.get("headings", 0)
            toc = structure.get("toc", [])
            lines.append(f"  outline: {headings} headings, {structure.get('sections', 0)} sections")
            if toc:
                lines.append(f"  toc: {_join_truncated(toc)}")
        if "match_count" in structure:
            lines.append(f"  matches: {structure['match_count']} in {structure.get('file_count', 0)} files")
            files = structure.get("files", [])
            if files:
                lines.append(f"  files: {_join_truncated(files)}")
        if "cmd" in structure:
            lines.append(f"  cmd: {structure['cmd']}")
            if structure.get("stdout_head"):
                lines.append(f"  stdout: {structure['stdout_head']}")
            if structure.get("stderr_head"):
                lines.append(f"  stderr: {structure['stderr_head']}")
            if structure.get("exit_code", 0) != 0:
                lines.append(f"  exit: {structure['exit_code']}")
        if "shape" in structure:
            lines.append(f"  shape: {structure['shape']}")
        if summary:
            lines.append(f"  summary: {summary}")
        if record.get("sensitive"):
            lines.append("  ⚠ sensitive")
        lines.append(f"  recall({short_id}) for content, recall({short_id}, \"q\") for slice")
        return "\n".join(lines)

    # full-toc
    # Same as outline but with all exports/imports/toc items
    if "exports" in structure:
        lines.append(f"  exports: {', '.join(structure['exports']) or '(none)'}")
    if "imports" in structure:
        lines.append(f"  imports: {', '.join(structure['imports']) or '(none)'}")
    if "toc" in structure:
        lines.append(f"  toc: {' | '.join(structure['toc']) or '(none)'}")
    if "shape" in structure:
        lines.append(f"  shape: {structure['shape']}")
    if summary:
        lines.append(f"  summary: {summary}")
    if record.get("sensitive"):
        lines.append("  ⚠ sensitive")
    lines.append(f"  recall({short_id}) for content, recall({short_id}, \"q\") for slice")
    return "\n".join(lines)


def _format_args(args: dict[str, Any]) -> str:
    """Compact representation of a tool's args."""
    if not args:
        return ""
    # Prefer file_path, pattern, cmd, query in that order
    for key in ("file_path", "pattern", "cmd", "query", "command"):
        if key in args:
            val = str(args[key])
            if len(val) > 50:
                val = val[:47] + "…"
            return f"{key}={val!r}"
    # Fallback: first key
    k, v = next(iter(args.items()))
    val = str(v)
    if len(val) > 50:
        val = val[:47] + "…"
    return f"{k}={val!r}"


def _join_truncated(items: list[str], max_len: int = 240) -> str:
    """Join items with commas, truncating if the joined string gets too long."""
    if not items:
        return "(none)"
    out = ", ".join(items)
    if len(out) <= max_len:
        return out
    return out[: max_len - 1] + "…"
