# Context Paging — Empirical Research

This directory contains a small pilot study of the [Context Pager](../) system,
plus the infrastructure to run a larger version of the study.

## Contents

- **`paper/PAPER.md`** — The full research writeup, including methodology,
  results, limitations, and reproducibility instructions. The paper
  documents a **pilot** (n=2 per cell) that was not adequately powered to
  answer the original research question, but does establish infrastructure
  and a key negative finding.
- **`harness/`** — The trial infrastructure: `page.py` (the pager interface
  the agent should use), `tasks.py` (the 4 task definitions), `run_trial.py`
  (single-trial runner), `run_batch.py` (batch orchestration and grading).
- **`corpora/small/`** — The Python web service corpus used in the pilot,
  including hidden `_grader/` test files for T1, T2, T3.
- **`results/pilot-trial-results.json`** — The 24 trial results from the
  pilot, including exec grader scores, token accounting, and T4 judge scores.
- **`results/pilot-trial-results.csv`** — Same data in CSV form for
  easy analysis.

## Headline result (and the headline caveat)

All 18 objective trials (T1, T2, T3 × 3 conditions × 2 trials) **passed 100%
of executable tests** in every condition. No quality degradation from
paging. But the pilot also found that **the subagents did not use the
pager** — they used their native `read_file` tool instead of the `page.py`
script. The PAGED and NULL-PAGER conditions were therefore functionally
identical to BASELINE for the agent.

A valid follow-up study would enforce the pager at the tool-harness
level, not as a prompt instruction.

## Reproducing

```bash
cd /path/to/context-paging/research/harness

# Generate the corpus
python3 build_corpus.py  # or just use the checked-in corpora/small/

# Run a single trial
python3 run_trial.py --task T1 --mode BASELINE --trial-num 0 \
    --output /tmp/trial.json

# Run the full pilot (3 conditions × 4 tasks × 2 trials = 24 trials)
# This requires spawning a subagent for each trial (see PAPER.md).
```

## Why n=2

The reviewer (sub-agent methodological review) suggested n=20+ per cell for
a powered study, with a 5-trial-per-cell variance pilot first. This pilot
ran 2 trials per cell — too small for a powered test, but enough to validate
the apparatus and surface the pager-bypass problem. See PAPER.md for the
recommended next-step sample size.
