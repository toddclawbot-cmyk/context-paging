"""
The page.py tool — the agent's interface to the corpus.

This script acts as a stand-in for the agent's normal Read/Grep tools when
in PAGED or NULL-PAGER mode. The agent calls this script via bash:

  python3 page.py read <file_path>
  python3 page.py grep <pattern>
  python3 page.py glob <pattern>
  python3 page.py recall <stash_id> [--query <q>] [--range <a>-<b>]
  python3 page.py list

For BASELINE mode, the agent uses normal Read/Grep/Glob tools.

The pager state (stash store, condition) is determined by the environment
variable PAGER_MODE and PAGER_STASH_DIR. The trial runner sets these.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------------
# Pager mode handling
# ----------------------------------------------------------------------------

PAGER_MODE = os.environ.get("PAGER_MODE", "BASELINE")
PAGER_STASH_DIR = os.environ.get("PAGER_STASH_DIR", "/tmp/pager-stash")
PAGER_THRESHOLD = int(os.environ.get("PAGER_THRESHOLD", "300"))
PAGER_CORPUS_ROOT = os.environ.get("PAGER_CORPUS_ROOT", ".")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ----------------------------------------------------------------------------
# Tool implementations (same as driver.py)
# ----------------------------------------------------------------------------

def read_file(rel_path: str) -> str:
    p = Path(PAGER_CORPUS_ROOT) / rel_path.lstrip("/")
    if not p.exists() or not p.is_file():
        return f"[error: file not found: {rel_path}]"
    return p.read_text()


def tool_glob(pattern: str) -> str:
    import fnmatch
    out = []
    for p in Path(PAGER_CORPUS_ROOT).rglob("*"):
        if p.is_file() and fnmatch.fnmatch(str(p.relative_to(PAGER_CORPUS_ROOT)), pattern):
            out.append(str(p.relative_to(PAGER_CORPUS_ROOT)))
    return "\n".join(sorted(out)) if out else "(no matches)"


def tool_grep(pattern: str) -> str:
    rgx = re.compile(pattern, re.IGNORECASE)
    out = []
    for p in sorted(Path(PAGER_CORPUS_ROOT).rglob("*")):
        if not p.is_file() or p.suffix in (".pyc", ".pyo"):
            continue
        try:
            text = p.read_text()
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rgx.search(line):
                rel = p.relative_to(PAGER_CORPUS_ROOT)
                out.append(f"{rel}:{i}:{line}")
    return "\n".join(out[:200]) if out else "(no matches)"


# ----------------------------------------------------------------------------
# Pager integration
# ----------------------------------------------------------------------------

def pager_intercept(tool: str, args: dict, content: str) -> tuple[str, str | None, int]:
    """
    If PAGER_MODE is BASELINE, return content unchanged.
    Otherwise, route through the pager and return (seen_text, stash_id_or_None, seen_size).
    """
    if PAGER_MODE == "BASELINE":
        return content, None, len(content)

    # Add the paging project to path
    PAGING = os.environ.get("CONTEXT_PAGING_PATH",
                            "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging")
    if PAGING not in sys.path:
        sys.path.insert(0, PAGING)

    from src.wrapper import ContextPager, InterceptorConfig

    config = InterceptorConfig(threshold_tokens=PAGER_THRESHOLD)
    pager = ContextPager(PAGER_STASH_DIR, config=config)
    seen = pager.intercept(tool, args, content)
    if seen == content:
        return content, None, len(content)
    # Find the stash id
    recs = list(pager.store.list_all())
    if recs:
        return seen, recs[-1]["id"], len(seen)
    return seen, None, len(seen)


def pager_recall(stash_id: str, mode: str = "full", query: str | None = None,
                 range_: tuple[int, int] | None = None) -> str:
    """
    Recall the content of a stashed tool result.

    For NULL-PAGER mode, return garbage to test whether recall is load-bearing.
    """
    if PAGER_MODE == "BASELINE":
        return "[error: recall not available in BASELINE mode]"

    if PAGER_MODE == "NULL-PAGER":
        # Log the recall attempt
        _log_access(stash_id, "recall_attempted_null_pager")
        return (
            "[NULL-PAGER] Recall disabled. The contents of this file are unavailable "
            "in this experimental condition. If you need the actual code, the recall "
            "tool is broken. Make your best guess based on the file's stub and any "
            "context you have."
        )

    # PAGED mode — actually recall
    PAGING = os.environ.get("CONTEXT_PAGING_PATH",
                            "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging")
    if PAGING not in sys.path:
        sys.path.insert(0, PAGING)

    from src.recall import recall as do_recall
    pager_store_path = Path(PAGER_STASH_DIR)
    from src.store import StashStore
    store = StashStore(pager_store_path)
    try:
        result = do_recall(store, stash_id, mode=mode, query=query)
        content = result.content if hasattr(result, "content") else str(result)
        # Log the recall
        _log_access(stash_id, "recalled", content_size=len(content))
        return content
    except Exception as e:
        _log_access(stash_id, "recall_error", error=str(e))
        return f"[recall error: {e}]"


def _log_access(stash_id: str, action: str, content_size: int = 0, error: str = ""):
    """Log a recall/recall-attempt to the access log for token accounting."""
    log_path = Path(PAGER_STASH_DIR) / "access.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "stash_id": stash_id,
        "action": action,
        "content_size": content_size,
    }
    if error:
        entry["error"] = error
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _log_view(tool: str, target: str, raw_size: int, seen_size: int, stash_id: str | None):
    """Log a view (read/grep/glob) for token accounting."""
    log_path = Path(PAGER_STASH_DIR) / "views.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "tool": tool,
        "target": target,
        "raw_chars": raw_size,
        "seen_chars": seen_size,
        "stashed": stash_id is not None,
        "stash_id": stash_id,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def cmd_read(args):
    rel = args.path
    content = read_file(rel)
    seen, stash_id, seen_size = pager_intercept("Read", {"file_path": rel}, content)
    _log_view("Read", rel, len(content), seen_size, stash_id)
    if stash_id:
        print(f"[STASHED: {stash_id}]")
    print(seen)


def cmd_grep(args):
    pattern = args.pattern
    content = tool_grep(pattern)
    seen, stash_id, seen_size = pager_intercept("Grep", {"pattern": pattern}, content)
    _log_view("Grep", pattern, len(content), seen_size, stash_id)
    if stash_id:
        print(f"[STASHED: {stash_id}]")
    print(seen)


def cmd_glob(args):
    pattern = args.pattern
    content = tool_glob(pattern)
    seen, stash_id, seen_size = pager_intercept("Glob", {"pattern": pattern}, content)
    _log_view("Glob", pattern, len(content), seen_size, stash_id)
    if stash_id:
        print(f"[STASHED: {stash_id}]")
    print(seen)


def cmd_recall(args):
    content = pager_recall(args.stash_id, mode=args.mode, query=args.query)
    print(content)


def cmd_list(args):
    """List all stashes with their stubs (PAGED mode only)."""
    if PAGER_MODE == "BASELINE":
        print("(no stashes in BASELINE mode)")
        return
    if PAGER_MODE == "NULL-PAGER":
        print("(stashes exist but recall is disabled in NULL-PAGER mode)")
        return
    PAGING = os.environ.get("CONTEXT_PAGING_PATH",
                            "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging")
    if PAGING not in sys.path:
        sys.path.insert(0, PAGING)
    from src.store import StashStore
    store = StashStore(Path(PAGER_STASH_DIR))
    for rec in store.list_all():
        print(f"[stash:{rec['id']}] {rec.get('tool', '?')}({json.dumps(rec.get('args', {}))})")
        # Print the stub
        try:
            stub = (Path(PAGER_STASH_DIR) / "stub.txt").read_text()
            print(stub)
            print("---")
        except FileNotFoundError:
            pass


def cmd_status(args):
    """Show current pager mode and configuration."""
    print(json.dumps({
        "mode": PAGER_MODE,
        "stash_dir": PAGER_STASH_DIR,
        "threshold_tokens": PAGER_THRESHOLD,
        "corpus_root": PAGER_CORPUS_ROOT,
    }, indent=2))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("read")
    pr.add_argument("path")
    pr.set_defaults(func=cmd_read)

    pg = sub.add_parser("grep")
    pg.add_argument("pattern")
    pg.set_defaults(func=cmd_grep)

    pgl = sub.add_parser("glob")
    pgl.add_argument("pattern")
    pgl.set_defaults(func=cmd_glob)

    prc = sub.add_parser("recall")
    prc.add_argument("stash_id")
    prc.add_argument("--mode", default="full")
    prc.add_argument("--query", default=None)
    prc.set_defaults(func=cmd_recall)

    pl = sub.add_parser("list")
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("status")
    ps.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
