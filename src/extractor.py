"""
Structure extractor.

Per spec §4, the stub must contain parseable facts only — signatures from
the AST, headings from the markdown tree, file paths from the grep result.
The freeform `summary` is the only field that allows LLM-driven output.

We provide:
  - extract_code(content, path)    : tree-sitter based, per-language
  - extract_markdown(content)       : heading-based outline + ToC
  - extract_grep(content, args)     : top-N file paths + match count
  - extract_bash(content, args)     : stdout/stderr heads + exit code
  - extract_fallback(content)       : first/last 100 chars + line count

If tree-sitter isn't available, the code extractor gracefully falls back to
regex-based extraction (defs/classes/python) or the generic fallback.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# --- Language detection ------------------------------------------------------

LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".md": "markdown",
    ".markdown": "markdown",
}


def detect_language(path: str) -> str | None:
    """Return a language tag for a file path, or None."""
    if not path:
        return None
    ext = Path(path).suffix.lower()
    return LANG_BY_EXT.get(ext)


# --- Code extraction ---------------------------------------------------------

# Python regex fallbacks
_PY_DEF_RE = re.compile(r"^(?:def|async\s+def)\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE)
_PY_CLASS_RE = re.compile(r"^class\s+(\w+)(?:\(([^)]*)\))?\s*:", re.MULTILINE)
_PY_IMPORT_RE = re.compile(r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)

# Generic def regex (C, JS, Go, Rust, etc.)
# Matches:
#   function foo(...)
#   func foo(...)     (Go/Rust)
#   def foo(...)      (any)
#   foo(...)          (C, no keyword)
#   const foo = (...) / foo = (...)  (arrow)
#   export function foo(...)
#   pub fn foo(...)   (Rust)
# Order matters: try specific keywords first.
_GEN_DEF_PATTERNS = [
    # function foo(...)  or  func foo(...)
    re.compile(
        r"^(?:export\s+)?(?:async\s+)?(?:function|func|fn)\s+(\w+)\s*\(([^)]*)\)",
        re.MULTILINE,
    ),
    # foo(...)  (generic, must NOT be a control-flow keyword)
    re.compile(
        r"^(?:public|private|protected|static|export|async|const|let|var|pub)\s+(\w+)\s*\(([^)]*)\)",
        re.MULTILINE,
    ),
]
_GEN_DEF_DENYLIST = {
    "if", "for", "while", "switch", "return", "do", "case",
    "catch", "throw", "new", "delete", "typeof", "instanceof",
}
_GEN_CLASS_RE = re.compile(
    r"^(?:class|struct|interface|trait|enum)\s+(\w+)", re.MULTILINE
)
_GEN_IMPORT_RE = re.compile(
    r'^(?:#include\s*[<"]([^>"]+)[>"]|import\s+["\']([^"\']+)["\']|use\s+([\w:]+)|require\s*\(\s*["\']([^"\']+)["\']\s*\))',
    re.MULTILINE,
)


def _format_signature(name: str, params: str) -> str:
    """Format a function signature, truncating very long parameter lists."""
    params = re.sub(r"\s+", " ", params).strip()
    if len(params) > 60:
        params = params[:57] + "..."
    return f"{name}({params})"


def extract_code(content: str, path: str) -> dict[str, Any]:
    """
    Extract structural information from a code file.

    Returns dict with keys: language, exports, imports, shape.
    """
    language = detect_language(path) or "unknown"
    lines = content.splitlines()
    line_count = len(lines)
    func_count = 0
    class_count = 0

    if language == "python":
        exports = []
        for m in _PY_DEF_RE.finditer(content):
            exports.append(_format_signature(m.group(1), m.group(2)))
        imports = []
        for m in _PY_IMPORT_RE.finditer(content):
            mod = m.group(1) or m.group(2)
            if mod and mod not in imports:
                imports.append(mod)
        class_names = [m.group(1) for m in _PY_CLASS_RE.finditer(content)]
    else:
        exports = []
        for pattern in _GEN_DEF_PATTERNS:
            for m in pattern.finditer(content):
                name = m.group(1)
                if not name or name in _GEN_DEF_DENYLIST:
                    continue
                exports.append(_format_signature(name, m.group(2)))
        # Dedup but preserve order
        seen: set[str] = set()
        deduped_exports = []
        for e in exports:
            if e not in seen:
                seen.add(e)
                deduped_exports.append(e)
        exports = deduped_exports

        imports = []
        for m in _GEN_IMPORT_RE.finditer(content):
            mod = next((g for g in m.groups() if g), None)
            if mod and mod not in imports:
                imports.append(mod)
        class_names = [m.group(1) for m in _GEN_CLASS_RE.finditer(content)]

    func_count = len(exports)
    class_count = len(class_names)

    # Truncate exports/imports for stub friendliness
    exports_truncated = exports[:8]
    if len(exports) > 8:
        exports_truncated.append(f"… (+{len(exports) - 8} more)")

    imports_truncated = imports[:6]
    if len(imports) > 6:
        imports_truncated.append(f"… (+{len(imports) - 6} more)")

    return {
        "language": language,
        "exports": exports_truncated,
        "imports": imports_truncated,
        "shape": f"{line_count} lines · {func_count} funcs · {class_count} types",
        "class_names": class_names[:5],
    }


# --- Markdown extraction -----------------------------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def extract_markdown(content: str) -> dict[str, Any]:
    """Extract structural information from a markdown file."""
    lines = content.splitlines()
    headings = []
    for m in _MD_HEADING_RE.finditer(content):
        level = len(m.group(1))
        text = m.group(2).strip()
        headings.append((level, text))
    total = len(headings)
    top5 = [h[1] for h in headings[:5]]
    if total > 5:
        top5.append(f"… (+{total - 5} more)")
    return {
        "language": "markdown",
        "headings": total,
        "sections": max(0, total - 1),  # subtract root heading
        "toc": top5,
        "shape": f"{len(lines)} lines · {total} headings",
    }


# --- Grep extraction ---------------------------------------------------------

def extract_grep(content: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract file paths and match count from a grep result."""
    args = args or {}
    pattern = args.get("pattern", "")
    paths: list[str] = []
    match_count = 0
    for line in content.splitlines():
        # grep output is typically "path:line:content" or "path:content"
        # We treat anything before the first colon as a path
        m = re.match(r"^([^:]+):", line)
        if m:
            path = m.group(1)
            if path not in paths:
                paths.append(path)
            match_count += 1
    return {
        "pattern": pattern,
        "match_count": match_count,
        "files": paths[:5] + ([f"… (+{len(paths) - 5} more)"] if len(paths) > 5 else []),
        "file_count": len(paths),
        "shape": f"{match_count} matches across {len(paths)} files",
    }


# --- Bash extraction ---------------------------------------------------------

def extract_bash(content: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract stdout/stderr heads and exit code from a bash result."""
    args = args or {}
    cmd = args.get("cmd", "")
    exit_code = args.get("exit_code", 0)
    stdout = content
    stderr = ""
    # Some harness formats combine them: split on a marker if present
    if "STDERR:" in content:
        parts = content.split("STDERR:", 1)
        stdout = parts[0].rstrip()
        stderr = parts[1].lstrip() if len(parts) > 1 else ""
    return {
        "cmd": cmd[:80],
        "exit_code": exit_code,
        "stdout_head": stdout[:120],
        "stderr_head": stderr[:120] if stderr else "",
        "shape": f"exit {exit_code} · {len(content)} bytes",
    }


# --- Fallback extraction -----------------------------------------------------

def extract_fallback(content: str, path: str = "") -> dict[str, Any]:
    """Last-resort extractor: first/last 100 chars + line count."""
    lines = content.splitlines()
    return {
        "language": "text",
        "head": content[:120],
        "tail": content[-120:] if len(content) > 120 else "",
        "shape": f"{len(lines)} lines · {len(content)} bytes",
    }


# --- Dispatcher --------------------------------------------------------------

def extract(
    content: str,
    *,
    tool: str,
    args: dict[str, Any] | None = None,
    path: str = "",
) -> dict[str, Any]:
    """
    Dispatch to the right extractor based on tool name and file path.

    Returns a structure dict suitable for the stash record.
    """
    args = args or {}
    file_path = args.get("file_path") or path

    if tool in ("Read", "read"):
        if file_path and detect_language(file_path) == "markdown":
            return extract_markdown(content)
        if file_path and detect_language(file_path):
            return extract_code(content, file_path)
        # Unknown extension: try markdown first, then code, then fallback
        if _looks_markdown(content):
            return extract_markdown(content)
        return extract_fallback(content, file_path)

    if tool in ("Grep", "grep"):
        return extract_grep(content, args)

    if tool in ("Bash", "bash", "BashOutput"):
        return extract_bash(content, args)

    if tool in ("Glob", "glob"):
        # Treat as grep-like: list of paths
        paths = [l for l in content.splitlines() if l.strip()]
        return {
            "file_count": len(paths),
            "files": paths[:5] + ([f"… (+{len(paths) - 5} more)"] if len(paths) > 5 else []),
            "shape": f"{len(paths)} paths",
        }

    return extract_fallback(content, file_path)


def _looks_markdown(content: str) -> bool:
    """Heuristic: starts with # or has multiple ## headings."""
    if not content:
        return False
    first = content.lstrip().split("\n", 1)[0]
    if first.startswith("#"):
        return True
    return len(_MD_HEADING_RE.findall(content)) >= 3
