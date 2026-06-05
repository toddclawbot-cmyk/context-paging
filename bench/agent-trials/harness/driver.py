#!/usr/bin/env python3
"""
Test harness driver for the context-paging system.

The driver is intentionally simple: it's a stateful object that the LLM
agent drives through tool calls. The driver records:
  - every tool call (tool, args, raw size, what the agent saw)
  - every recall call (which stash, mode, query, result size)
  - per-step and cumulative token cost

The driver supports two modes:
  - BASELINE: tool results returned verbatim (no stash)
  - PAGED:    tool results stashed when over threshold; recall() is available

The LLM (agent) interacts with the driver through a small protocol:
  - To call a tool:     print  `>>>TOOL: name | args=...`
  - To recall:          print  `>>>RECALL: stash_id | mode=full`
  - To give final ans:  print  `>>>ANSWER: ...` (multi-line ok, terminated by `<<<`)

A separate verifier (verifier.py) checks the answer against ground truth.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Add the paging project to path
PAGING = "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging"
sys.path.insert(0, PAGING)

from src.store import StashStore, estimate_tokens
from src.wrapper import ContextPager, InterceptorConfig
from src.recall import recall


CORPUS = Path("/tmp/paging-test-corpus")


# ----------------------------------------------------------------------------
# File reading — what the agent "sees" through the pager
# ----------------------------------------------------------------------------

def read_file_raw(rel_path: str) -> str:
    """Read a file from the corpus, returning empty string if missing."""
    p = CORPUS / rel_path.lstrip("/")
    if not p.exists() or not p.is_file():
        return f"[error: file not found: {rel_path}]"
    return p.read_text()


# ----------------------------------------------------------------------------
# Tool implementations
# ----------------------------------------------------------------------------

TOOL_DEFS = {
    "Read": {"args": ["file_path"]},
    "Glob": {"args": ["pattern"]},
    "Grep": {"args": ["pattern"]},
    "Bash": {"args": ["cmd"]},
}


def tool_glob(pattern: str) -> str:
    """Return matching file paths from the corpus."""
    import fnmatch
    out = []
    for p in CORPUS.rglob("*"):
        if p.is_file() and fnmatch.fnmatch(str(p.relative_to(CORPUS)), pattern):
            out.append(str(p.relative_to(CORPUS)))
    return "\n".join(sorted(out)) if out else "(no matches)"


def tool_grep(pattern: str) -> str:
    """Grep for a regex across all files in the corpus."""
    rgx = re.compile(pattern, re.IGNORECASE)
    out = []
    for p in sorted(CORPUS.rglob("*")):
        if not p.is_file() or p.suffix in (".pyc", ".pyo"):
            continue
        try:
            text = p.read_text()
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rgx.search(line):
                rel = p.relative_to(CORPUS)
                out.append(f"{rel}:{i}:{line}")
    return "\n".join(out[:200]) if out else "(no matches)"


def tool_bash(cmd: str) -> str:
    """Execute a (limited) bash command and return stdout+stderr."""
    safe = cmd.strip()
    # safety: no destructive ops, no network, no shell metachars that could escape
    if any(bad in safe for bad in ["rm -rf", "rm -fr", "sudo", "curl", "wget", " | sh", "&& rm", "; rm"]):
        return "[error: command rejected by sandbox]"
    try:
        import subprocess
        r = subprocess.run(safe, shell=True, capture_output=True, text=True, timeout=10, cwd=str(CORPUS))
        out = (r.stdout + r.stderr).strip()
        return out if out else "(no output, exit 0)"
    except subprocess.TimeoutExpired:
        return "[error: command timed out]"
    except Exception as e:
        return f"[error: {e}]"


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

@dataclass
class CallRecord:
    tool: str
    args: dict
    raw_size_chars: int
    seen_size_chars: int
    stashed: bool
    stash_id: str | None = None


@dataclass
class RecallRecord:
    stash_id: str
    mode: str
    query: str | None
    seen_size_chars: int


@dataclass
class TrialMetrics:
    mode: str                      # "BASELINE" or "PAGED"
    task_id: str
    calls: list[CallRecord] = field(default_factory=list)
    recalls: list[RecallRecord] = field(default_factory=list)
    final_answer: str = ""
    stop_reason: str = ""          # "answer", "max_calls", "agent_exit"

    @property
    def total_raw_tokens(self) -> int:
        return sum(c.raw_size_chars for c in self.calls) // 4

    @property
    def total_seen_tokens(self) -> int:
        # The "context cost" — what was actually added to the conversation
        s = sum(c.seen_size_chars for c in self.calls)
        s += sum(r.seen_size_chars for r in self.recalls)
        return s // 4

    @property
    def pass_through(self) -> int:
        return sum(1 for c in self.calls if not c.stashed)

    @property
    def stashed_count(self) -> int:
        return sum(1 for c in self.calls if c.stashed)

    @property
    def unique_stashes(self) -> int:
        return len({c.stash_id for c in self.calls if c.stash_id})


class Trial:
    """
    A single trial run. The agent drives it via stdout protocol.

    mode="BASELINE": tool results returned verbatim (full file content).
    mode="PAGED":    results go through the pager; agent can recall.

    The driver itself is just a Python object — there's no LLM inside.
    The LLM is the *external* agent (i.e. me) that reads the prompts and
    issues tool calls by writing to stdout.
    """

    def __init__(self, task_id: str, task_prompt: str, mode: str,
                 threshold: int = 300, max_calls: int = 50):
        self.task_id = task_id
        self.task_prompt = task_prompt
        self.mode = mode
        self.threshold = threshold
        self.max_calls = max_calls

        self.metrics = TrialMetrics(mode=mode, task_id=task_id)

        if mode == "PAGED":
            self.tmpdir = tempfile.mkdtemp(prefix=f"paging-{task_id}-")
            config = InterceptorConfig(threshold_tokens=threshold)
            self.pager = ContextPager(self.tmpdir, config=config)
            self.stash_known: dict[str, dict[str, str]] = {}  # stash_id -> {tool, args, summary}
        else:
            self.pager = None
            self.stash_known = {}

    # --- public API used by the agent (via stdout) ---

    def run_tool(self, tool: str, args: dict) -> dict:
        """Execute a tool call. Returns a dict the agent can render."""
        if len(self.metrics.calls) >= self.max_calls:
            return {"error": f"max_calls ({self.max_calls}) reached"}

        # Materialize the raw result
        if tool == "Read":
            content = read_file_raw(args.get("file_path", ""))
        elif tool == "Glob":
            content = tool_glob(args.get("pattern", "**/*"))
        elif tool == "Grep":
            content = tool_grep(args.get("pattern", ""))
        elif tool == "Bash":
            content = tool_bash(args.get("cmd", ""))
        else:
            content = f"[error: unknown tool {tool}]"

        raw_size = len(content)

        # Paged: route through pager
        if self.pager is not None and tool != "recall":
            seen = self.pager.intercept(tool, args, content)
            stashed = (seen != content)
            stash_id = None
            if stashed:
                # Look up the most recent record to get the stash id
                recs = list(self.pager.store.list_all())
                if recs:
                    stash_id = recs[-1]["id"]
                    self.stash_known[stash_id] = {
                        "tool": tool,
                        "args": args,
                        "stub": seen,
                    }
        else:
            seen = content
            stashed = False
            stash_id = None

        rec = CallRecord(
            tool=tool, args=args, raw_size_chars=raw_size,
            seen_size_chars=len(seen), stashed=stashed, stash_id=stash_id,
        )
        self.metrics.calls.append(rec)

        return {
            "tool": tool,
            "args": args,
            "stashed": stashed,
            "stash_id": stash_id,
            "seen": seen,
        }

    def run_recall(self, stash_id: str, mode: str = "full", query: str | None = None) -> dict:
        if self.pager is None:
            return {"error": "recall is not available in BASELINE mode"}
        result = recall(self.pager.store, stash_id, mode=mode, query=query)
        rec = RecallRecord(
            stash_id=stash_id, mode=mode, query=query,
            seen_size_chars=len(result.content) if hasattr(result, "content") else 0,
        )
        self.metrics.recalls.append(rec)
        return {
            "stash_id": stash_id,
            "mode": mode,
            "content": result.content if hasattr(result, "content") else str(result),
            "truncated": getattr(result, "truncated", False),
        }

    def list_stashes(self) -> str:
        """Return the stubs of all stashes the agent has seen (PAGED only)."""
        if not self.stash_known:
            return "(no stashes yet)"
        out = []
        for sid, meta in self.stash_known.items():
            out.append(f"[stash:{sid}] {meta['tool']}({json.dumps(meta['args'])})\n{meta['stub']}")
        return "\n---\n".join(out)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main():
    """Driver CLI. Reads commands from stdin, writes results to stdout."""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True, help="task id: 1, 2, or 3")
    p.add_argument("--mode", required=True, choices=["BASELINE", "PAGED"])
    p.add_argument("--threshold", type=int, default=300)
    p.add_argument("--max-calls", type=int, default=50)
    p.add_argument("--output", help="path to write final metrics JSON")
    args = p.parse_args()

    task_id = args.task
    # Read ground truth task prompts
    gt = (CORPUS / "GROUND_TRUTH.md").read_text()
    task_prompts = {
        "1": "Find the security concern in the authentication flow. Identify it and explain why it's a problem. Cite specific lines and files.",
        "2": "Implement a per-user rate limit of 30 requests/minute on all authenticated endpoints. What files would you change and how?",
        "3": "A user reports their cart has items but POST /orders/checkout returns 400 with the message 'cart is empty' — but they just added items. Find the root cause.",
    }
    task_prompt = task_prompts[task_id]

    trial = Trial(task_id=task_id, task_prompt=task_prompt,
                  mode=args.mode, threshold=args.threshold,
                  max_calls=args.max_calls)

    # Print the system + task header
    print("=" * 70)
    print(f"  TRIAL  task={task_id}  mode={args.mode}  threshold={args.threshold} tok")
    print("=" * 70)
    print()
    print("## Task")
    print(task_prompt)
    print()
    print("## Available tools")
    print("- Read(file_path)   — read a file from /tmp/paging-test-corpus")
    print("- Glob(pattern)     — find files by pattern (e.g. 'src/*.py')")
    print("- Grep(pattern)     — search for regex across all files")
    print("- Bash(cmd)         — run a shell command (sandboxed)")
    if args.mode == "PAGED":
        print("- recall(stash_id, mode='full'|'extract'|'lines', query=..., range=(a,b))")
    print()
    print("## Protocol")
    print("To call a tool, print a line:")
    print("  >>>TOOL: name | args_json")
    print("To recall (PAGED only):")
    print("  >>>RECALL: stash_id | mode=full")
    print("To list all known stashes (PAGED only):")
    print("  >>>STASHES")
    print("To submit your final answer:")
    print("  >>>ANSWER:")
    print("  ... (multi-line) ...")
    print("  <<<")
    print()
    print("=" * 70)
    print("BEGIN. Issue tool calls below.")
    print("=" * 70)
    sys.stdout.flush()

    # Read the agent's commands
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            continue
        if line.startswith(">>>TOOL:"):
            try:
                payload = line[len(">>>TOOL:"):].strip()
                tool, rest = payload.split("|", 1)
                tool = tool.strip()
                targs = json.loads(rest.split("args=", 1)[1].strip())
            except Exception as e:
                print(f"[parse error: {e}]")
                sys.stdout.flush()
                continue
            result = trial.run_tool(tool, targs)
            if result.get("stashed"):
                print(f"[stashed as {result['stash_id']}]")
            print(result["seen"])
            print("---")
            sys.stdout.flush()
        elif line.startswith(">>>RECALL:"):
            try:
                payload = line[len(">>>RECALL:"):].strip()
                parts = [p.strip() for p in payload.split("|")]
                stash_id = parts[0]
                mode = "full"
                for pp in parts[1:]:
                    if pp.startswith("mode="):
                        mode = pp.split("=", 1)[1]
                result = trial.run_recall(stash_id, mode=mode)
                if "error" in result:
                    print(result["error"])
                else:
                    print(result["content"])
            except Exception as e:
                print(f"[recall error: {e}]")
            print("---")
            sys.stdout.flush()
        elif line.startswith(">>>STASHES"):
            print(trial.list_stashes())
            print("---")
            sys.stdout.flush()
        elif line.startswith(">>>ANSWER:"):
            buf = []
            for next_line in sys.stdin:
                if next_line.rstrip("\n") == "<<<":
                    break
                buf.append(next_line.rstrip("\n"))
            trial.metrics.final_answer = "\n".join(buf)
            trial.metrics.stop_reason = "answer"
            break
        elif line.startswith(">>>EXIT"):
            trial.metrics.stop_reason = "agent_exit"
            break
        else:
            # echo agent's own notes (e.g. think-aloud) but don't act on them
            pass

    # Write metrics
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps({
            "task_id": trial.metrics.task_id,
            "mode": trial.metrics.mode,
            "stop_reason": trial.metrics.stop_reason,
            "call_count": len(trial.metrics.calls),
            "pass_through": trial.metrics.pass_through,
            "stashed_count": trial.metrics.stashed_count,
            "unique_stashes": trial.metrics.unique_stashes,
            "recall_count": len(trial.metrics.recalls),
            "total_raw_tokens": trial.metrics.total_raw_tokens,
            "total_seen_tokens": trial.metrics.total_seen_tokens,
            "token_savings_pct": (
                100 * (1 - trial.metrics.total_seen_tokens / max(1, trial.metrics.total_raw_tokens))
            ),
            "calls": [
                {"tool": c.tool, "args": c.args, "raw": c.raw_size_chars,
                 "seen": c.seen_size_chars, "stashed": c.stashed, "stash_id": c.stash_id}
                for c in trial.metrics.calls
            ],
            "recalls": [
                {"stash_id": r.stash_id, "mode": r.mode, "query": r.query,
                 "seen": r.seen_size_chars}
                for r in trial.metrics.recalls
            ],
            "final_answer": trial.metrics.final_answer,
        }, indent=2))

    # Final summary to stdout
    print()
    print("=" * 70)
    print("  TRIAL COMPLETE")
    print("=" * 70)
    print(f"  Calls:        {len(trial.metrics.calls)}")
    print(f"  Stashed:      {trial.metrics.stashed_count} (unique: {trial.metrics.unique_stashes})")
    print(f"  Recalls:      {len(trial.metrics.recalls)}")
    print(f"  Raw tokens:   {trial.metrics.total_raw_tokens:,}")
    print(f"  Seen tokens:  {trial.metrics.total_seen_tokens:,}")
    saved = trial.metrics.total_raw_tokens - trial.metrics.total_seen_tokens
    pct = 100 * saved / max(1, trial.metrics.total_raw_tokens)
    print(f"  Savings:      {saved:,} ({pct:.1f}%)")
    print()


if __name__ == "__main__":
    main()
