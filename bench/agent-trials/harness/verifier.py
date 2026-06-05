#!/usr/bin/env python3
"""
Verifier: takes a trial output JSON and grades the final answer
against the ground truth for that task.

Uses a subagent (delegate_task) so the grading is independent
of the agent that produced the answer.
"""
from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path

CORPUS = Path("/tmp/paging-test-corpus")


def grade(task_id: str, mode: str, answer: str, metrics: dict) -> dict:
    gt_text = (CORPUS / "GROUND_TRUTH.md").read_text()
    # Extract just the relevant task section
    lines = gt_text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"## Task {task_id} "):
            start = i
            break
    if start is None:
        raise ValueError(f"Task {task_id} not found in ground truth")
    # Find the next "## Task" line
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## Task "):
            end = i
            break
    expected = "\n".join(lines[start:end])

    # Build the grading prompt
    grading_prompt = f"""You are a strict, fair grading judge. An agent was given a coding question and
produced an answer. Score the answer against the ground truth on the
following rubric.

GROUND TRUTH (what a correct answer should contain):
---
{expected}
---

AGENT'S ANSWER (mode: {mode}):
---
{answer}
---

Score the answer on these axes, each 0-2:

  RECALL (0-2): Did the agent identify the key technical facts?
    2 = all key facts present and accurate
    1 = some key facts present, partial accuracy
    0 = missed the point entirely

  CITATION (0-2): Did the agent cite specific files/lines (not just vibes)?
    2 = specific file paths AND function/symbol names from the corpus
    1 = file paths only, or function names only
    0 = no specific citations

  CORRECTNESS (0-2): Is what they say *correct* (no fabricated details)?
    2 = everything stated is accurate
    1 = mostly accurate, minor errors that don't change the conclusion
    0 = contains significant errors or hallucinations

  REASONING (0-2): Is the explanation clear and well-structured?
    2 = clear, well-reasoned, action-oriented
    1 = understandable but rushed or thin
    0 = confused, contradictory, or incoherent

Reply with JSON only, no prose:
{{
  "recall": <0|1|2>,
  "citation": <0|1|2>,
  "correctness": <0|1|2>,
  "reasoning": <0|1|2>,
  "total": <sum>,
  "max": 8,
  "missing_facts": ["<fact 1>", "<fact 2>"],
  "wrong_claims": ["<claim 1>"],
  "verdict": "<one-sentence summary>"
}}
"""
    # Run a subagent to grade
    # (We're not actually calling delegate_task from this script —
    # the parent agent will call delegate_task and pass results back.
    # This script just provides the prompt and parses results.)

    return {
        "task_id": task_id,
        "mode": mode,
        "expected": expected,
        "answer": answer,
        "grading_prompt": grading_prompt,
    }


def parse_grade(text: str) -> dict:
    """Parse a JSON grade response, tolerating markdown code fences."""
    text = text.strip()
    if text.startswith("```"):
        # strip code fence
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


if __name__ == "__main__":
    # Used in standalone mode for debugging
    if len(sys.argv) < 4:
        print("usage: verifier.py <task_id> <mode> <metrics.json>")
        sys.exit(1)
    task_id, mode, metrics_path = sys.argv[1], sys.argv[2], sys.argv[3]
    m = json.loads(Path(metrics_path).read_text())
    out = grade(task_id, mode, m["final_answer"], m)
    print(json.dumps(out, indent=2))
