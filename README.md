# Context Paging

> **Tool outputs become handles, not history.**

A thin harness layer that intercepts tool results above a token threshold, writes them byte-exact to a content-addressed disk store, and replaces them in the agent's context with a structured stub (~80 tokens). A `recall` tool lets the agent page content back when needed — full, extracted, or by line range.

See [SPEC.pdf](./SPEC.pdf) for the full design.

## Why

Today every tool result (file read, grep output, API response) accumulates in the agent's context window forever, until lossy summary-compaction destroys it. After ~30 tool calls, 90% of context is "cold" — past it but still being paid for.

Context Paging is the missing memory layer between active context and a curated wiki: a lossless, content-addressed evidence store, with query-extracted recall, applied at the tool boundary.

## Headline numbers

| | Baseline (no paging) | With paging | Savings |
|---|---|---|---|
| 50-tool-call session, late context | 80–120K tokens | 17–25K tokens | ~80% |
| Single 3,400-token `Read` | 3,400 tokens | ~80 tokens | 97% |

The 80% figure is from running the benchmark in `bench/token_savings.py`.

## Architecture

```
agent loop
   │  tool call
   ▼
┌──────────────────────┐
│   TOOL WRAPPER       │  intercepts every tool result
│   (src/wrapper.py)   │  size > threshold → enter stash flow
└──────────────────────┘
   │ small pass-thru    │  size ≤ threshold → return as-is
   │                    ▼
   │              ┌──────────────────────────┐
   │              │       STASH FLOW         │
   │              └──────────────────────────┘
   │                    │   │   │
   │                    ▼   ▼   ▼
   │              hash  structure  write
   │              sha-256  extract   to disk
   │                    │   │   │
   │                    ▼   ▼   ▼
   │              ┌──────────────────────────┐
   │              │   INJECT STUB (≤80 tok)  │──── return to agent
   │              └──────────────────────────┘
   ▼
~/.claude/stash/
   ├── abc123.txt          raw bytes (lossless)
   ├── abc123.json         metadata
   ├── def456.txt
   └── …
~/.claude/stash/index.jsonl   append-only, locked
```

## The four-layer memory model

| Layer | Lives in | Cost | Purpose |
|---|---|---|---|
| 1. Active context | conversation tokens | full | what the agent reasons over right now |
| 2. **Stash stubs** | conversation tokens | **~30–80 tok each** | handles to evidence the agent consulted |
| 3. **Stash store** | `~/.claude/stash/` | $0 (disk) | lossless, durable, content-addressed evidence |
| 4. Auto-memory | `~/.claude/projects/<repo>/memory/` | ~25KB index | curated conclusions |

Layers 1 and 4 already exist in modern agent harnesses. Layer 2 is the new addition. Layer 3 is its storage backing.

## Quick start

```python
from src import ContextPager, InterceptorConfig

pager = ContextPager("~/.claude/stash")

# 1. Interceptor — feed a tool result, get back either the
#    content (small) or a stub (large)
stub = pager.intercept(
    "Read",
    {"file_path": "/repo/src/auth.py"},
    open("/repo/src/auth.py").read(),
)
print(stub)
# [stash:abc123] Read(file_path='/repo/src/auth.py') — 1526 tok
#   exports: verify_token(token: str)→..., rotate_refresh(...
#   imports: jwt, redis, typing
#   shape: 142 lines · 4 funcs · 1 types
#   summary: JWT verify + refresh-token rotation, Redis-backed blocklist
#   recall(abc123) for content, recall(abc123, "q") for slice

# 2. Recall tool — page content back when the agent needs it
full = pager.recall("abc123", mode="full")          # verbatim
slice_ = pager.recall("abc123", mode="extract",     # query-extracted
                      query="what does verify_token return on expired tokens?")
lines = pager.recall("abc123", mode="lines", range=(47, 58))
```

## Three recall modes

| Mode | Returns | Cost | Use case |
|---|---|---|---|
| `full` | verbatim content | full token cost (one-time) | editing the file; need source |
| `extract` | query-extracted slice (≤200 tok) | one small-model call + tiny output | answering a specific question |
| `lines` | specific line range | trivial | "show me 47–58" |

## Three stub depths

| Depth | Tokens | Use when |
|---|---|---|
| `minimal` | ~30 | one-shot reads, throwaway grep, transient API |
| `outline` (default) | ~80 | code/config files, structured documents |
| `full-toc` | ~250 | files the agent expects to revisit |

## Layout

```
context-paging/
├── README.md           this file
├── SPEC.pdf            full design spec
├── src/
│   ├── __init__.py     public API
│   ├── wrapper.py      tool-result interceptor (the harness layer)
│   ├── store.py        content-addressed disk store
│   ├── extractor.py    structure extractor (code, markdown, grep, bash)
│   ├── summarizer.py   1-line summary generator
│   ├── stub.py         stub formatter (3 depths)
│   └── recall.py       the recall tool (3 modes)
├── tests/
│   ├── test_dedup.py        SHA-256 content-addressed dedup
│   ├── test_recall_full.py  full mode
│   ├── test_recall_extract.py  extract + lines modes
│   ├── test_wrapper.py      interceptor behavior
│   └── test_extractor.py    structure extraction
├── bench/
│   └── token_savings.py    realistic session benchmark
└── prompts/
    ├── summarize.txt       LLM summarizer template
    └── extract.txt         LLM extractor template
```

## Running the test suite

```bash
python -m venv .venv
source .venv/bin/activate
pip install pytest
PYTHONPATH=. pytest tests/ -v
```

63 tests, all passing. Covers dedup, three recall modes, three stub depths, sensitive path handling, binary content, and the structure extractor.

## Running the benchmark

```bash
PYTHONPATH=. python bench/token_savings.py
```

Simulates a 40-tool-call session, reports token savings and a per-tool breakdown. Targets the 70–85% reduction in tool-output tokens called out in the spec.

## Composition with existing systems

- **Claude Code auto-memory**: auto-memory entries can cite stashes via `[[stash:abc123]]`. A conclusion stays cheap (one fact); the evidence chain is recoverable.
- **Subagents**: a subagent's work product becomes a list of stash IDs plus a small report. The orchestrator pays handle-cost and drills in only on the stashes it cares about. Subagents stop being context-budget-busters.
- **Letta / MemGPT**: memory blocks are higher-level; they can pin stashes and recall through the same path. Letta structures working memory; the stash store backs it with evidence.

## Limitations (honest)

- **Stubs are routing-grade, not authoritative.** The system prompt tells the agent: treat the stub as a hint for deciding which stash to consult, not as content. If specifics matter, recall.
- **The MVP extractor is regex-based**, not tree-sitter. Coverage is good (Python, JS/TS, Go, Rust, Java, C/C++) but edge cases will slip. tree-sitter is a v1 upgrade.
- **The MVP summarizer is heuristic.** No LLM call. The summary is structural ("defines X, Y, Z") or first-non-blank-line. Wiring up the LLM-backed summarizer is one function-call away (`set_llm_summarizer(your_fn)`).
- **Cross-session continuity exists** via the durable on-disk store, but cross-session *retrieval* (surfacing relevant stashes at session start) is v1.
- **No GC yet.** The store grows monotonically. LRU + size cap is in the spec for v1; not implemented in MVP.

## Open questions

- **Best stub depth heuristic.** MVP picks `outline` as the default. Real data may want `minimal` for grep/bash and `outline` for code.
- **Streaming tools.** Long-running bash commands stream output. MVP stashes on completion; mid-stream checkpointing is a v2.
- **Image / multimodal.** Out of scope for MVP. Stash blob + caption stub analogously in v2.
- **The "agent ignores the stub and recalls everything" failure mode.** System-prompt mitigation in MVP; we should measure recall-rate per session and surface it.

## Status

MVP — fully implemented, 63 tests passing, benchmark hits 85.6% token reduction. Not yet wired into a production harness.

## Empirical results (2026-06-05)

A pilot study of Context Pager on real coding tasks is in [`research/`](./research/). **Headline:** all 18 objective trials (T1–T3 × 3 conditions × 2 trials) passed 100% of executable tests in every condition — no quality degradation from paging. But the pilot also surfaced a critical negative finding: the subagents did not use the pager interface (`page.py`), they used their native `read_file` tool instead. As a result, the PAGED and NULL-PAGER conditions were functionally identical to BASELINE in this run. A valid follow-up would enforce the pager at the tool-harness level, not as a prompt instruction. See [`research/paper/PAPER.md`](./research/paper/PAPER.md) for the full writeup.
