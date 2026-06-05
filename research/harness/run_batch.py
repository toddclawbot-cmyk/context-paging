"""
Pilot study runner for context-paging research.

Phase 1: Generate trial workdirs (clean, unfixed corpus copies)
Phase 2: For each trial, return the prompt for the orchestrator to send to a subagent
Phase 3: After all subagents complete, run graders

The orchestrator (this LLM session) does the actual subagent spawning via
delegate_task. This script just sets up the infrastructure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import tasks as task_module


PAGING_PATH = "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging"
CORPORA_ROOT = Path(__file__).parent.parent / "corpora"
RESULTS_DIR = Path(__file__).parent.parent / "results"
HARNESS_DIR = Path(__file__).parent
PAGE_PY = HARNESS_DIR / "page.py"

# Ensure results dir exists
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def setup_trial(corpus_name: str, condition: str, trial_id: str, threshold: int = 300) -> dict:
    """
    Create a clean trial workdir with the unfixed corpus.
    Returns the workdir info needed by the subagent.
    """
    workdir = Path(tempfile.mkdtemp(prefix=f"trial-{trial_id}-"))

    # Source corpus (always clean)
    src = CORPORA_ROOT / corpus_name
    for item in src.iterdir():
        if item.name == 'GROUND_TRUTH.md':
            continue
        if item.name.startswith('.'):
            continue
        if item.is_dir():
            shutil.copytree(item, workdir / item.name)
        else:
            shutil.copy2(item, workdir / item.name)

    # Stash dir
    stash_dir = workdir / ".stash"
    stash_dir.mkdir()

    # Copy page.py
    shutil.copy(PAGE_PY, workdir / "page.py")

    return {
        "workdir": str(workdir),
        "stash_dir": str(stash_dir),
        "env": {
            "PAGER_MODE": condition,
            "PAGER_STASH_DIR": str(stash_dir),
            "PAGER_THRESHOLD": str(threshold),
            "PAGER_CORPUS_ROOT": str(workdir),  # corpus is now the workdir root
        },
    }


def build_trial_prompt(task, setup: dict) -> str:
    condition = setup["env"]["PAGER_MODE"]
    workdir = setup["workdir"]

    prompt = f"""You are an LLM coding agent in a research study. Experimental condition: **{condition}**.

WORKING DIRECTORY: {workdir}
(All corpus files are in this directory: src/, tests/, docs/.)

== HOW TO READ FILES ==

If {condition} == "BASELINE":
  Use your normal `read_file` tool on paths relative to {workdir} (e.g. "src/settings.py").
  Tool results are returned verbatim.

If {condition} in ("PAGED", "NULL-PAGER"):
  You MUST go through the pager. Use bash:
    python3 {workdir}/page.py read <path>     (instead of read_file)
    python3 {workdir}/page.py grep <pattern>  (instead of search/grep)
    python3 {workdir}/page.py glob <pattern>  (instead of glob)
    python3 {workdir}/page.py list            (to see all stashes)
    python3 {workdir}/page.py recall <stash_id>  (to get full content of a stashed result)
  Do NOT use `cat`, `head`, or `read_file` directly on files — the pager needs to see every tool result.

== THE TASK ==

{task.question}

== DELIVERABLE ==

Write your final answer to: {workdir}/SOLUTION.json

If the write_file tool refuses paths under /var/ (system temp), use bash heredoc:
  cat > {workdir}/SOLUTION.json << 'EOF'
  {{ "files_modified": [...], "explanation": "..." }}
  EOF

== CONSTRAINTS ==

- Do not read files in tests/_grader/ — those are for grading.
- Do not modify any test files (anything under tests/ that's not _grader is fine to read, but don't edit tests/).
- Be efficient with tool calls.
- When done, reply with "DONE" and a one-sentence summary of what you changed.
"""
    return prompt


def grade_trial(setup: dict, task, trial_id: str, agent_summary: str) -> dict:
    """Grade a completed trial (after the subagent has written SOLUTION.json)."""
    workdir = Path(setup["workdir"])

    # Read the solution
    solution_path = workdir / "SOLUTION.json"
    if solution_path.exists():
        try:
            answer = json.loads(solution_path.read_text())
        except json.JSONDecodeError:
            answer = {"_error": "could not parse SOLUTION.json",
                      "_raw": solution_path.read_text()[:2000]}
    else:
        answer = {"_error": "no SOLUTION.json written",
                  "_agent_summary": agent_summary or ""}

    # Run executable grader
    exec_grader = grade_executable(workdir, task)

    # Run LLM judge
    llm_judge = grade_llm(answer, task)

    # Token accounting from the stash store
    tokens = account_tokens(setup)

    return {
        "trial_id": trial_id,
        "task_id": task.id,
        "task_type": task.task_type,
        "corpus": task.corpus,
        "condition": setup["env"]["PAGER_MODE"],
        "workdir": str(workdir),
        "answer": answer,
        "executable_grader": exec_grader,
        "llm_judge": llm_judge,
        "tokens": tokens,
        "agent_summary_tail": (agent_summary or "")[-500:],
    }


def grade_executable(workdir: Path, task) -> dict:
    if task.grader_test_path is None:
        return {"applicable": False, "skipped": "subjective task"}
    test_path = workdir / task.grader_test_path
    if not test_path.exists():
        return {"applicable": False, "skipped": f"grader test not found: {test_path}"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-v", "--tb=line",
             "--rootdir", str(workdir), "-q"],
            cwd=str(workdir),
            capture_output=True, text=True,
            env={**os.environ, "JWT_SECRET": "test-grader-secret-32-chars-x"},
            timeout=60,
        )
        output = proc.stdout + "\n" + proc.stderr
        num_passed = output.count(" PASSED")
        num_failed = output.count(" FAILED")
        return {
            "applicable": True,
            "passed": num_failed == 0 and num_passed > 0,
            "num_passed": num_passed,
            "num_failed": num_failed,
            "output": output[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"applicable": True, "passed": False, "error": "timeout"}
    except Exception as e:
        return {"applicable": True, "passed": False, "error": str(e)}


def grade_llm(answer: dict, task, judge_model: str = "MiniMax-M2.7") -> dict:
    if task.rubric is None:
        return {"applicable": False}
    criteria_desc = "\n".join(
        f"- **{name}** (weight {info['weight']}): {info['description']}"
        for name, info in task.rubric.items()
    )
    prompt = f"""You are a strict, independent code-review judge. Score an agent's answer against a rubric.

RUBRIC:
{criteria_desc}

SCORING (per criterion):
  0 = missed it / wrong
  1 = partially addressed, with some accuracy
  2 = fully addressed, accurate, well-cited

THE TASK:
{task.question}

THE AGENT'S ANSWER (JSON):
{json.dumps(answer, indent=2)[:6000]}

Reply with strict JSON only. No prose, no markdown fences:
{{
  "scores": {{"<criterion_name>": <0|1|2>, ...}},
  "weighted_total": <float>,
  "max_score": <float>,
  "overall_verdict": "<one sentence>"
}}"""
    try:
        raw = call_judge(prompt, judge_model)
        return {
            "applicable": True,
            "judge_model": judge_model,
            "raw_response": raw,
            "scores": parse_judge(raw),
        }
    except Exception as e:
        return {"applicable": True, "error": str(e)}


def call_judge(prompt: str, model: str = "MiniMax-M2.7") -> str:
    import urllib.request
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        r = subprocess.run(['bash', '-c', 'source /Users/chaz/.hermes/.env 2>/dev/null; echo $MINIMAX_API_KEY'],
                            capture_output=True, text=True)
        api_key = r.stdout.strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set")
    body = {
        "model": model,
        "max_tokens": 1000,
        "system": "You are a strict, fair code-review judge. Always respond with valid JSON only. No prose, no markdown fences.",
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.minimax.io/anthropic/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
        for c in d.get("content", []):
            if c.get("type") == "text":
                return c.get("text", "")
        return ""


def parse_judge(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "parse_error": True}


def account_tokens(setup: dict) -> dict:
    """
    Read the views.jsonl and access.jsonl to compute token accounting.
    """
    stash_dir = Path(setup["stash_dir"])
    views_path = stash_dir / "views.jsonl"
    access_path = stash_dir / "access.jsonl"

    raw_total = 0
    seen_total = 0
    n_views = 0
    n_stashed_views = 0

    if views_path.exists():
        for line in views_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_total += rec.get("raw_chars", 0)
            seen_total += rec.get("seen_chars", 0)
            n_views += 1
            if rec.get("stashed"):
                n_stashed_views += 1

    # Recall content is additional seen content
    n_recalls = 0
    recall_content_chars = 0
    if access_path.exists():
        for line in access_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("action") == "recalled":
                n_recalls += 1
                recall_content_chars += rec.get("content_size", 0)

    seen_total_with_recalls = seen_total + recall_content_chars
    return {
        "applicable": n_views > 0,
        "raw_chars": raw_total,
        "seen_chars": seen_total,
        "raw_tokens": raw_total // 4,
        "seen_tokens": seen_total // 4,
        "savings_pct_no_recall": 100 * (1 - seen_total / max(1, raw_total)),
        "n_views": n_views,
        "n_stashed_views": n_stashed_views,
        "n_recalls": n_recalls,
        "recall_content_chars": recall_content_chars,
        "recall_content_tokens": recall_content_chars // 4,
        "seen_tokens_with_recalls": seen_total_with_recalls // 4,
        "savings_pct_with_recalls": 100 * (1 - seen_total_with_recalls / max(1, raw_total)),
    }


def generate_pilot_setups(tasks_to_run: list[str], conditions: list[str],
                          n_per_cell: int, threshold: int = 300) -> list[dict]:
    """Generate all trial workdirs and prompts. Returns list of trial configs."""
    all_setups = []
    for task_id in tasks_to_run:
        task = task_module.all_tasks()[task_id]
        for condition in conditions:
            for n in range(n_per_cell):
                trial_id = f"{task_id}-{condition}-{n}"
                setup = setup_trial(task.corpus, condition, trial_id, threshold)
                prompt = build_trial_prompt(task, setup)
                all_setups.append({
                    "trial_id": trial_id,
                    "task_id": task_id,
                    "condition": condition,
                    "trial_num": n,
                    "task": task,
                    "setup": setup,
                    "prompt": prompt,
                })
    return all_setups


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--generate-pilot", action="store_true")
    p.add_argument("--tasks", nargs="+", default=["T1", "T2", "T3", "T4"])
    p.add_argument("--conditions", nargs="+", default=["BASELINE", "PAGED", "NULL-PAGER"])
    p.add_argument("--n-per-cell", type=int, default=3)
    p.add_argument("--threshold", type=int, default=300)
    p.add_argument("--output", default=None, help="Where to write the trial configs JSON")
    args = p.parse_args()

    if args.generate_pilot:
        setups = generate_pilot_setups(args.tasks, args.conditions, args.n_per_cell, args.threshold)
        # Don't serialize the task object (not JSON-safe)
        for s in setups:
            s.pop("task", None)
        out = args.output or str(RESULTS_DIR / "pilot_setups.json")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(setups, indent=2))
        print(f"Generated {len(setups)} trial setups -> {out}")
        print(f"Tasks: {args.tasks}")
        print(f"Conditions: {args.conditions}")
        print(f"N per cell: {args.n_per_cell}")


if __name__ == "__main__":
    main()
