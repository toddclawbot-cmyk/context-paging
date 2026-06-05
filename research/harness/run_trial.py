"""
Trial runner for a single (corpus, task, condition) trial.

Sets up an isolated workdir with a copy of the corpus, configures the pager
mode (BASELINE / PAGED / NULL-PAGER), spawns a subagent via Claude Code,
captures the subagent's actions and outputs, runs the executable grader,
and (if the task is subjective) calls an independent LLM judge.

Output: a single JSON file per trial with all measurements.
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


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

PAGING_PATH = os.environ.get(
    "CONTEXT_PAGING_PATH",
    "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging",
)
CORPORA_ROOT = Path(__file__).parent.parent / "corpora"
RESULTS_DIR = Path(__file__).parent.parent / "results"
HARNESS_DIR = Path(__file__).parent
PAGE_PY = HARNESS_DIR / "page.py"


# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------

def setup_workdir(corpus_name: str, condition: str, trial_id: str, threshold: int = 300) -> Path:
    """
    Create an isolated workdir for the trial.
    Returns the workdir path.
    """
    workdir = Path(tempfile.mkdtemp(prefix=f"trial-{trial_id}-"))
    corpus_src = CORPORA_ROOT / corpus_name / "_project" if (CORPORA_ROOT / corpus_name / "_project").exists() else CORPORA_ROOT / corpus_name
    corpus_dst = workdir / "corpus"
    shutil.copytree(corpus_src, corpus_dst)

    # Stash dir for the pager
    stash_dir = workdir / ".stash"
    stash_dir.mkdir()

    # Page.py setup — copy page.py into the workdir so the agent can use it
    shutil.copy(PAGE_PY, workdir / "page.py")

    # Set environment for the subagent
    env_file = workdir / ".env"
    env_file.write_text(
        f"PAGER_MODE={condition}\n"
        f"PAGER_STASH_DIR={stash_dir}\n"
        f"PAGER_THRESHOLD={threshold}\n"
        f"PAGER_CORPUS_ROOT={corpus_dst}\n"
    )
    return workdir


# ----------------------------------------------------------------------------
# Subagent invocation
# ----------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """\
You are an LLM agent with a strict tool budget. You have access to:
  - bash (run any shell command)
  - file read/write
  - search/grep

Your goal: complete the task described by the user. When you're done, write
your final answer to /tmp/SOLUTION.json (or to the workdir-relative path
SOLUTION.json).

CRITICAL — read this carefully:

Depending on the experimental condition (set via env var PAGER_MODE), the way
you interact with the corpus changes:

* If PAGER_MODE=BASELINE: use normal tools (Read, Grep, Glob, Bash) as you
  would in any other environment. Tool results are returned verbatim.

* If PAGER_MODE=PAGED or PAGER_MODE=NULL-PAGER: tool results are routed
  through a pager. Files above the threshold return stubs with a [stash:ID]
  marker. To get the full content, use:
    python3 ./page.py recall <stash_id>
  Or list all stashes:
    python3 ./page.py list
  For all other file operations, use:
    python3 ./page.py read <path>
    python3 ./page.py grep <pattern>
    python3 ./page.py glob <pattern>
  If you would normally use `cat <file>`, use `python3 ./page.py read <file>`
  instead. If you would normally use `rg` or `grep`, use
  `python3 ./page.py grep <pattern>`.

DO NOT read files directly (cat, head, etc.) in PAGED or NULL-PAGER modes —
always go through page.py. The pager needs to see every tool result.

Be efficient. You have a limited budget of tool calls. Plan before you act.
"""


def run_subagent(workdir: Path, task_question: str, env: dict, agent_model: str = "haiku") -> dict:
    """
    Spawn a subagent in the given workdir to do the task.

    Returns a dict with: transcript (tool calls), final_answer, duration_sec.
    """
    start = time.time()

    # The agent works in the workdir. We pass it the task as a single prompt
    # and capture its full transcript by recording what page.py produces.
    #
    # For real implementation, we use a delegate_task that runs Claude Code
    # in a worktree. But for now, we use a simpler approach: the agent runs
    # the task with a timeout, and we capture stdout/stderr.
    #
    # In the MVP, we use claude-code CLI as the agent.
    cmd = [
        "claude",
        "--model", agent_model,
        "--system-prompt", AGENT_SYSTEM_PROMPT,
        "--max-turns", "30",
        task_question,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=600,
    )
    duration = time.time() - start
    return {
        "transcript": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
        "duration_sec": duration,
    }


# ----------------------------------------------------------------------------
# Grading
# ----------------------------------------------------------------------------

def run_executable_grader(workdir: Path, task) -> dict:
    """
    Run the task's executable grader test.
    Returns a dict with: passed, num_passed, num_failed, output.
    """
    if task.grader_test_path is None:
        return {"applicable": False, "skipped": "subjective task"}

    test_path = workdir / "corpus" / task.grader_test_path
    if not test_path.exists():
        return {
            "applicable": False,
            "skipped": f"grader test not found: {test_path}",
        }

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-v", "--tb=short",
             "--rootdir", str(workdir / "corpus")],
            cwd=str(workdir / "corpus"),
            capture_output=True,
            text=True,
            env={**os.environ, "JWT_SECRET": "test-grader-secret-32-chars-x"},
            timeout=60,
        )
        output = proc.stdout + "\n" + proc.stderr
        # Parse pass/fail counts
        num_passed = output.count(" PASSED")
        num_failed = output.count(" FAILED")
        return {
            "applicable": True,
            "passed": num_failed == 0 and num_passed > 0,
            "num_passed": num_passed,
            "num_failed": num_failed,
            "output": output[-3000:],  # last 3KB
        }
    except subprocess.TimeoutExpired:
        return {"applicable": True, "passed": False, "error": "timeout"}
    except Exception as e:
        return {"applicable": True, "passed": False, "error": str(e)}


def run_llm_judge(answer: dict, task, judge_model: str = "MiniMax-M2.7") -> dict:
    """
    Score the agent's answer against the task rubric using an independent LLM.
    Returns a dict with: per_criterion scores, total, max.
    """
    if task.rubric is None:
        return {"applicable": False}

    # Build the grading prompt
    criteria_desc = "\n".join(
        f"- **{name}** (weight {info['weight']}): {info['description']}"
        for name, info in task.rubric.items()
    )

    prompt = f"""You are a strict, independent code-review judge. Score an agent's answer against a rubric.

RUBRIC:
{criteria_desc}

SCORING SCALE (per criterion):
  0 = missed it entirely or wrong
  1 = partially addressed, with some accuracy
  2 = fully addressed, accurate, well-cited

THE TASK:
{task.question}

THE AGENT'S ANSWER (JSON):
{json.dumps(answer, indent=2)}

GROUND TRUTH (for reference only — you should judge based on the answer, not by comparison):
{task.ground_truth}

Reply with strict JSON only, no prose, no markdown fences. Format:
{{
  "scores": {{"<criterion_name>": <0|1|2>, ...}},
  "per_criterion_reasoning": {{"<criterion_name>": "one sentence", ...}},
  "weighted_total": <float>,
  "max_score": <float>,
  "overall_verdict": "<one sentence>"
}}"""

    # Call the judge (MiniMax-M2.7)
    try:
        result_text = call_judge_model(prompt, judge_model)
        return {
            "applicable": True,
            "judge_model": judge_model,
            "raw_response": result_text,
            "scores": _parse_judge_response(result_text, task.rubric),
        }
    except Exception as e:
        return {
            "applicable": True,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


def call_judge_model(prompt: str, model: str = "MiniMax-M2.7") -> str:
    """Call the LLM judge. Uses the Anthropic-format endpoint."""
    import urllib.request

    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        # Try to load from .env
        result = subprocess.run(['bash', '-c', 'source /Users/chaz/.hermes/.env 2>/dev/null; echo $MINIMAX_API_KEY'],
                                capture_output=True, text=True)
        api_key = result.stdout.strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set")

    body = {
        "model": model,
        "max_tokens": 1500,
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


def _parse_judge_response(text: str, rubric: dict) -> dict:
    """Parse the judge's JSON response. Strip markdown fences if present."""
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
        return {"raw": text, "parse_error": "could not parse"}


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def run_trial(task_id: str, condition: str, trial_num: int, threshold: int = 300,
              agent_model: str = "haiku", judge_model: str = "MiniMax-M2.7") -> dict:
    task = task_module.all_tasks()[task_id]

    # Setup workdir
    workdir = setup_workdir(task.corpus, condition, f"{task_id}-{condition}-{trial_num}", threshold)

    # Setup env for the agent
    env = {
        "PAGER_MODE": condition,
        "PAGER_STASH_DIR": str(workdir / ".stash"),
        "PAGER_THRESHOLD": str(threshold),
        "PAGER_CORPUS_ROOT": str(workdir / "corpus"),
    }

    # Run the subagent
    print(f"  Running subagent for {task_id} {condition} trial {trial_num}...")
    agent_result = run_subagent(workdir, task.question, env, agent_model)

    # Read the agent's solution
    solution_path = workdir / "SOLUTION.json"
    if solution_path.exists():
        try:
            answer = json.loads(solution_path.read_text())
        except json.JSONDecodeError:
            answer = {"_error": "could not parse SOLUTION.json",
                      "_raw": solution_path.read_text()[:2000]}
    else:
        answer = {"_error": "no SOLUTION.json written",
                  "_agent_output_tail": agent_result["transcript"][-2000:]}

    # Run the executable grader
    print(f"  Running executable grader...")
    grader_result = run_executable_grader(workdir, task)

    # Run the LLM judge (if subjective or always for the qualitative component)
    print(f"  Running LLM judge ({judge_model})...")
    judge_result = run_llm_judge(answer, task, judge_model)

    # Build the result record
    result = {
        "trial_id": f"{task_id}-{condition}-{trial_num}",
        "task_id": task_id,
        "task_type": task.task_type,
        "corpus": task.corpus,
        "condition": condition,
        "trial_num": trial_num,
        "threshold_tokens": threshold,
        "agent_model": agent_model,
        "judge_model": judge_model,
        "timestamp": time.time(),
        "duration_sec": agent_result["duration_sec"],
        "agent_returncode": agent_result["returncode"],
        "answer": answer,
        "executable_grader": grader_result,
        "llm_judge": judge_result,
        # Save workdir path so we can re-grade later if needed
        "workdir": str(workdir),
    }

    # Cleanup workdir
    shutil.rmtree(workdir, ignore_errors=True)

    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True)
    p.add_argument("--condition", required=True, choices=["BASELINE", "PAGED", "NULL-PAGER"])
    p.add_argument("--trial-num", type=int, required=True)
    p.add_argument("--threshold", type=int, default=300)
    p.add_argument("--agent-model", default="haiku")
    p.add_argument("--judge-model", default="MiniMax-M2.7")
    p.add_argument("--output", required=True)
    args = p.parse_args()

    result = run_trial(
        task_id=args.task,
        condition=args.condition,
        trial_num=args.trial_num,
        threshold=args.threshold,
        agent_model=args.agent_model,
        judge_model=args.judge_model,
    )

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2))

    # Print summary
    print(f"\n  RESULT: task={args.task} cond={args.condition} trial={args.trial_num}")
    print(f"  duration: {result['duration_sec']:.1f}s")
    print(f"  exec grader: passed={result['executable_grader'].get('passed')}  "
          f"({result['executable_grader'].get('num_passed', 0)} passed, "
          f"{result['executable_grader'].get('num_failed', 0)} failed)")
    if "scores" in result["llm_judge"] and isinstance(result["llm_judge"]["scores"], dict):
        scores = result["llm_judge"]["scores"]
        weighted = scores.get("weighted_total", "?")
        max_s = scores.get("max_score", "?")
        print(f"  LLM judge: weighted_total={weighted} / {max_s}")


if __name__ == "__main__":
    main()
