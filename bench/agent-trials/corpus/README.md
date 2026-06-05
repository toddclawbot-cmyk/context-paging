# Agent Trial Corpus

The `_project/` subdirectory contains a synthetic Python web service used as
the test corpus for context-paging A/B trials. See:

- `_project/GROUND_TRUTH.md` — the 3 tasks and their planted correct answers
- `../harness/build_corpus.py` — regenerates this directory
- `../harness/run_trial.py` — runs the trials

## Files in `_project/`

- `src/` — 17 Python modules (settings, auth, cart, orders, api_*, deps, main, etc.)
- `tests/` — 3 pytest files
- `docs/api.md` — endpoint reference
- `README.md` — project description

## Tasks

1. **Security review** — find the hardcoded JWT secret default in
   `src/settings.py` and explain why it's a problem.
2. **Multi-file refactor** — design a per-user rate-limit (30 req/min) on
   authenticated endpoints, citing which files to change.
3. **Bug investigation** — diagnose why `POST /orders/checkout` returns 400
   "cart is empty" when the user has items.

## Size

~8,200 tokens total across 23 files. With a 300-tok stash threshold, ~7
files get stashed and ~16 pass through.
