#!/usr/bin/env python3
"""
Token-savings benchmark.

Per spec §12.2, the target is 70–85% reduction in tool-output tokens for a
realistic session. This script:

  1. Simulates a 50-tool-call session with realistic outputs
  2. Computes baseline context cost (all tool outputs in full)
  3. Computes paged context cost (small outputs pass-through, large → stubs)
  4. Reports the delta and a few operational metrics

No network, no model calls. Pure arithmetic on simulated tool results.
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
from dataclasses import dataclass, field

# Add src/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.store import StashStore
from src.wrapper import ContextPager, InterceptorConfig


@dataclass
class SimulatedToolCall:
    """A simulated tool call + result."""
    tool: str
    args: dict
    content: str


@dataclass
class SessionMetrics:
    """Aggregated metrics for one session."""
    call_count: int = 0
    raw_tokens_total: int = 0
    context_tokens_total: int = 0        # what the agent actually saw
    pass_through_count: int = 0           # small results, returned as-is
    stashed_count: int = 0                # results that became stubs
    total_stub_tokens: int = 0            # sum of stub sizes
    unique_stashes: int = 0
    recall_calls: int = 0
    recall_tokens: int = 0
    per_call: list[dict] = field(default_factory=list)


def make_realistic_session(seed: int = 42) -> list[SimulatedToolCall]:
    """Generate a realistic 50-tool-call session for a code agent."""
    rng = random.Random(seed)
    calls: list[SimulatedToolCall] = []

    # 30 distinct file reads
    files_read = []
    for i in range(20):
        path = f"src/module_{i}.py"
        files_read.append(path)
        # 80% are large (multi-func files), 20% are small
        if rng.random() < 0.8:
            content = make_large_python_file(i)
        else:
            content = make_small_python_file(i)
        calls.append(SimulatedToolCall("Read", {"file_path": path}, content))

    # 5 of the files get re-read (dedup test)
    for _ in range(5):
        path = rng.choice(files_read)
        # Find the matching content
        for c in calls:
            if c.args.get("file_path") == path:
                calls.append(SimulatedToolCall("Read", {"file_path": path}, c.content))
                break

    # 8 grep operations
    for i in range(8):
        content = "\n".join(
            f"src/module_{j}.py:{k+1}:def func_{i}_{k}(): pass"
            for j in range(min(10, i + 1))
            for k in range(5)
        )
        calls.append(SimulatedToolCall("Grep", {"pattern": f"func_{i}_"}, content))

    # 5 bash commands
    for i in range(5):
        if i % 2 == 0:
            content = f"running test_{i}.py\nPASS test_{i}.py::test_a\nPASS test_{i}.py::test_b\n5 passed in 0.42s\n"
        else:
            content = f"total 24\ndrwxr-xr-x  3 user  staff    96 Jun  4 10:00 .\ndrwxr-xr-x  5 user  staff   160 Jun  4 10:00 ..\n-rw-r--r--  1 user  staff  1234 Jun  4 10:00 file_{i}.py\n"
        calls.append(SimulatedToolCall("Bash", {"cmd": f"echo run-{i}"}, content))

    # 2 glob operations
    for i in range(2):
        content = "\n".join(f"src/module_{j}.py" for j in range(15))
        calls.append(SimulatedToolCall("Glob", {"pattern": "**/*.py"}, content))

    # Shuffle to simulate real session order
    rng.shuffle(calls)
    return calls


def make_small_python_file(i: int) -> str:
    """A small Python file (~30 lines, ~75 tokens)."""
    return f'''"""Module {i}."""
import os

CONST_{i} = {i}

def helper_{i}(x):
    return x + {i}

class Thing_{i}:
    def __init__(self):
        self.value = {i}
'''


def make_large_python_file(i: int) -> str:
    """A large Python file (~200-300 lines, ~800-1500 tokens)."""
    parts = [f'"""Module {i} — large file for benchmark."""']
    parts.append(f"import os\nimport sys\nimport json\nfrom typing import Optional, List, Dict")
    for j in range(8):
        parts.append(f'''

def function_{i}_{j}(arg1: str, arg2: int, arg3: Optional[Dict] = None) -> List[str]:
    """Function {j} in module {i}.

    Does some interesting thing with its arguments.
    """
    if arg2 < 0:
        raise ValueError("arg2 must be non-negative")
    result = []
    for k in range(arg2):
        result.append(f"{{arg1}}-{{k}}")
    if arg3:
        result.append(json.dumps(arg3))
    return result


class Handler_{i}_{j}:
    """Handler class for {i}.{j}."""

    def __init__(self, name: str):
        self.name = name
        self.count = 0

    def process(self, items: List[str]) -> Dict[str, int]:
        counts = {{}}
        for item in items:
            counts[item] = counts.get(item, 0) + 1
        self.count += len(items)
        return counts
''')
    return "\n".join(parts)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def run_session(pager: ContextPager, calls: list[SimulatedToolCall]) -> SessionMetrics:
    """Run a simulated session through the pager."""
    m = SessionMetrics()
    for call in calls:
        raw_tokens = estimate_tokens(call.content)
        m.raw_tokens_total += raw_tokens
        m.call_count += 1

        # Hand to the pager (this is the cost the agent pays per call)
        result = pager.intercept(call.tool, call.args, call.content)
        result_tokens = estimate_tokens(result)
        m.context_tokens_total += result_tokens

        if result == call.content:
            m.pass_through_count += 1
        else:
            m.stashed_count += 1
            m.total_stub_tokens += result_tokens

        m.per_call.append({
            "tool": call.tool,
            "raw": raw_tokens,
            "ctx": result_tokens,
            "stashed": result != call.content,
        })

    m.unique_stashes = pager.store.list_count()
    return m


def run_recall_workload(pager: ContextPager, calls: list[SimulatedToolCall], n_recalls: int = 10) -> None:
    """Simulate the agent recalling a few stashes — verify it works."""
    from src.recall import recall
    records = list(pager.store.list_all())
    if not records:
        return
    rng = random.Random(99)
    for _ in range(n_recalls):
        rec = rng.choice(records)
        # Recall the full content
        result = recall(pager.store, rec["id"], mode="full")
        # Sanity: content is non-empty
        assert result.content, f"recall returned empty for {rec['id']}"


def print_report(m: SessionMetrics, threshold: int) -> None:
    """Print the benchmark report."""
    print()
    print("=" * 70)
    print("  CONTEXT PAGING — TOKEN SAVINGS BENCHMARK")
    print("=" * 70)
    print()
    print(f"  Tool calls:            {m.call_count}")
    print(f"  Stash threshold:       {threshold} tokens")
    print()
    print("  --- Output mix ---")
    print(f"  Pass-through (small):  {m.pass_through_count:>4} calls")
    print(f"  Stashed (large):       {m.stashed_count:>4} calls")
    print(f"  Unique stashes:        {m.unique_stashes:>4}")
    print()
    print("  --- Token cost ---")
    print(f"  Raw tool output:       {m.raw_tokens_total:>7,} tokens (baseline)")
    print(f"  Paged context cost:    {m.context_tokens_total:>7,} tokens (with stubbing)")
    saved = m.raw_tokens_total - m.context_tokens_total
    pct = 100.0 * saved / m.raw_tokens_total if m.raw_tokens_total else 0
    print(f"  Saved:                 {saved:>7,} tokens ({pct:.1f}%)")
    print()
    print("  --- Average stub size ---")
    if m.stashed_count:
        avg_stub = m.total_stub_tokens / m.stashed_count
        print(f"  Mean tokens per stub:  {avg_stub:.0f}")
        print(f"  Target:                ~80 tokens (outline depth)")
    print()
    print("  --- Per-tool breakdown ---")
    by_tool: dict[str, list[int]] = {}
    for c in m.per_call:
        by_tool.setdefault(c["tool"], []).extend([c["raw"], c["ctx"]])
    print(f"  {'tool':<10}  {'raw':>7}  {'ctx':>7}  {'savings':>8}")
    for tool, vals in sorted(by_tool.items()):
        raw_total = sum(vals[0::2])
        ctx_total = sum(vals[1::2])
        if raw_total == 0:
            continue
        saved_pct = 100 * (raw_total - ctx_total) / raw_total
        print(f"  {tool:<10}  {raw_total:>7,}  {ctx_total:>7,}  {saved_pct:>7.1f}%")
    print()
    print("  --- Verdict ---")
    if pct >= 70:
        print(f"  ✓ Hits the 70–85% target from SPEC §1 ({pct:.1f}%)")
    else:
        print(f"  ✗ Below target. Got {pct:.1f}%, target 70%+.")
        print(f"    (Probably need to lower the stash threshold or use more")
        print(f"    realistic large-output distributions.)")
    print()
    print("=" * 70)


def main():
    # Set up
    tmpdir = tempfile.mkdtemp(prefix="cp-bench-")
    threshold = 500
    config = InterceptorConfig(threshold_tokens=threshold)
    pager = ContextPager(tmpdir, config=config)

    # Generate + run
    print(f"Running simulated session in {tmpdir}...")
    calls = make_realistic_session()
    metrics = run_session(pager, calls)
    run_recall_workload(pager, calls, n_recalls=8)

    # Report
    print_report(metrics, threshold)

    # Cleanup
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
