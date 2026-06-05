# Agent Trials

Empirical A/B testing of context paging on real coding tasks. See
[`bench/EMPIRICAL-RESULTS.md`](../EMPIRICAL-RESULTS.md) for the full writeup.

## Quick start

```bash
# (Re)build the corpus
python3 harness/build_corpus.py

# Run all 6 trials
cd harness
for task in 1 2 3; do
  for mode in BASELINE PAGED; do
    python3 run_trial.py --task $task --mode $mode --output /tmp/trial.json
  done
done
```

## Layout

```
agent-trials/
├── README.md            this file
├── corpus/              the test corpus (Python web service, 23 files)
│   ├── README.md        corpus description
│   └── _project/        the actual code
│       ├── src/         17 Python modules
│       ├── tests/       3 test files
│       ├── docs/
│       ├── README.md
│       └── GROUND_TRUTH.md   the 3 tasks + their planted correct answers
└── harness/             the test driver
    ├── build_corpus.py  regenerates corpus/ from a script
    ├── driver.py        core Trial class — the test harness
    ├── run_trial.py     per-(mode, task) plans and the runner
    ├── preview.py       quick preview of paged-mode stubs (Task 1)
    ├── preview3.py      quick preview of paged-mode stubs (Task 3)
    └── verifier.py      LLM-judge grading prompt generator
```

## What it does

The harness runs a single task at a time through the `ContextPager` and
records:

- Tool call count, stash count, unique stashes, recall count
- Raw tokens (sum of all tool outputs) and seen tokens (what the agent
  actually received)
- Per-call breakdown: which calls stashed, which were recalled
- Final answer text

The output is a JSON file. The full empirical writeup is in
`bench/EMPIRICAL-RESULTS.md`.

## Headline result (2026-06-05)

| Task | Mode | Raw tok | Seen tok | Savings | Quality (8) |
|---|---|---:|---:|---:|---:|
| 1 Security | BASELINE | 2,932 | 2,932 | 0% | 8 |
| 1 Security | **PAGED** | 2,932 | **868** | **70.4%** | **8** |
| 2 Refactor | BASELINE | 2,083 | 2,083 | 0% | 8 |
| 2 Refactor | **PAGED** | 2,083 | **973** | **53.3%** | **8** |
| 3 Debug | BASELINE | 3,065 | 3,065 | 0% | 8 |
| 3 Debug | **PAGED** | 3,065 | **2,039** | **33.5%** | **8** |

**Paging preserved answer quality (8/8 in every trial) and cut token
cost 34–73%** — without any tuning beyond setting a sensible threshold.

## How grading works

Each final answer is graded by an independent subagent (a fresh LLM with
no context about the trial) on 4 axes, 0–2 each:
- RECALL — did it cover the key technical facts?
- CITATION — did it cite specific files AND function/symbol names?
- CORRECTNESS — is what it says accurate, no hallucinations?
- REASONING — clear, well-structured, action-oriented?

The grading prompt is in `harness/verifier.py` if you want to re-run it.
