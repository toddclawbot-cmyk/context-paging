"""
Task instance definitions for the context-paging research study.

Each task instance is a specific, graded question against a specific corpus.
The grader is the combination of:
  - An executable test (for code-fix / refactor / feature-add tasks)
  - A decomposed LLM-judge rubric (for explanation / analysis tasks)

Task instances are paired across conditions: the same task ID runs in
BASELINE, PAGED, and NULL-PAGER modes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class TaskInstance:
    """A single graded task."""
    id: str                          # e.g. "small-T1"
    corpus: str                      # "small" or "medium"
    task_type: str                   # "code_fix" | "refactor" | "feature_add" | "explanation"
    question: str                    # the prompt given to the agent
    files_to_modify: list[str] = field(default_factory=list)  # files the agent may edit
    # For objective tasks: the grader runs a test file
    grader_test_path: str | None = None    # path inside the corpus to a pytest file
    grader_test_setup: str | None = None   # shell commands to set up the test env (e.g. install deps)
    # For subjective tasks: the rubric used by the LLM judge
    rubric: dict | None = None
    # The ground truth explanation (used to validate the LLM judge and to
    # compute agreement between human-graded and LLM-graded scores)
    ground_truth: str = ""
    # Files the agent MUST read to answer correctly (used to verify the
    # task is "doable" with the corpus)
    hint_files: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Small corpus task instances
# ----------------------------------------------------------------------------

SMALL_TASKS = {
    "T1": TaskInstance(
        id="small-T1",
        corpus="small",
        task_type="code_fix",
        question=(
            "The codebase has a security vulnerability in `src/settings.py`: "
            "the `load_settings()` function defaults `jwt_secret` to a hardcoded "
            "string `\"dev-secret-do-not-use-in-prod\"` if the `JWT_SECRET` env "
            "var is unset. An attacker who knows this string can forge valid "
            "JWTs and impersonate any user.\n\n"
            "Your job:\n"
            "1. Fix `src/settings.py` so that `load_settings()` raises a "
            "`RuntimeError` (or any exception) if `JWT_SECRET` is unset or empty. "
            "Remove the hardcoded default.\n"
            "2. In your final answer, briefly explain why the original code was "
            "a security risk (1-3 sentences).\n\n"
            "You can read and edit any file in the corpus. Do not modify test "
            "files. When you're done, write your final answer to "
            "`/tmp/<your-workdir>/SOLUTION.json` with the structure:\n"
            "```json\n"
            "{\n"
            '  "files_modified": ["src/settings.py", ...],\n'
            '  "explanation": "The original code ... was a security risk because ..."\n'
            "}\n"
            "```"
        ),
        files_to_modify=["src/settings.py"],
        grader_test_path="tests/_grader/test_T1.py",
        rubric={
            "code_change_correct": {
                "weight": 0.6,
                "description": "Did the agent remove the hardcoded default and raise on unset?",
            },
            "explanation_quality": {
                "weight": 0.4,
                "description": "Does the explanation correctly identify the security risk (hardcoded fallback leading to token forgery)?",
            },
        },
        ground_truth=(
            "The original `load_settings()` had a hardcoded fallback "
            "`jwt_secret=os.environ.get('JWT_SECRET', 'dev-secret-do-not-use-in-prod')`. "
            "This is a security risk because if `JWT_SECRET` is unset in production "
            "(forgotten during deploy, missed in env config, etc.), the application "
            "silently uses a known-public string to sign JWTs. Anyone who knows or "
            "guesses the string can mint valid tokens that `decode_access_token()` "
            "will accept, allowing them to impersonate any user including admins."
        ),
        hint_files=["src/settings.py", "src/auth.py"],
    ),
    "T2": TaskInstance(
        id="small-T2",
        corpus="small",
        task_type="refactor",
        question=(
            "The codebase already has a `RateLimiter` class in `src/rate_limit.py` "
            "but it is not actually enforced anywhere. Wire it into the "
            "authentication flow so that all authenticated endpoints enforce a "
            "per-user rate limit of 30 requests/minute.\n\n"
            "Specifically:\n"
            "1. Modify `src/deps.py` so that `get_current_user` enforces a "
            "30 req/min limit using the existing `RateLimiter`. The 30/min value "
            "should be respected (override the settings default of 60).\n"
            "2. Don't break the existing API: `get_current_user` should still "
            "return a `User` for valid tokens; it should raise HTTPException(429) "
            "for users that exceed the limit.\n\n"
            "You can read and edit any file. Do not modify test files. When "
            "done, write to `/tmp/<your-workdir>/SOLUTION.json` with:\n"
            "```json\n"
            "{\n"
            '  "files_modified": ["src/deps.py", ...],\n'
            '  "explanation": "I wired RateLimiter into get_current_user by ..."\n'
            "}\n"
            "```"
        ),
        files_to_modify=["src/deps.py", "src/rate_limit.py", "src/settings.py"],
        grader_test_path="tests/_grader/test_T2.py",
        rubric={
            "code_change_correct": {
                "weight": 0.7,
                "description": "Does get_current_user actually call a rate limiter that rejects after 30 requests/minute?",
            },
            "explanation_quality": {
                "weight": 0.3,
                "description": "Does the explanation correctly describe the wiring?",
            },
        },
        ground_truth=(
            "The fix is to instantiate a `RateLimiter(redis_client, limit_per_min=30)` "
            "in `get_current_user` and call `limiter.check(user.id)` after the user "
            "is resolved. The 30/min value can be hardcoded in the wiring or read "
            "from a new env var. The existing `RateLimiter` already raises "
            "HTTPException(429) on excess."
        ),
        hint_files=["src/deps.py", "src/rate_limit.py", "src/settings.py"],
    ),
    "T3": TaskInstance(
        id="small-T3",
        corpus="small",
        task_type="feature_add",
        question=(
            "Add a new function `enforce_strict_rate_limit(user_id: str, "
            "max_per_min: int) -> None` to `src/rate_limit.py`. It should use a "
            "SEPARATE Redis key prefix `rl-strict:` (not `rl:`) so that a stricter "
            "per-endpoint limit can be applied alongside the existing global "
            "limiter. The function should raise HTTPException(429) if the user "
            "exceeds `max_per_min` requests in the current minute.\n\n"
            "You can edit `src/rate_limit.py` and add the function. Do not modify "
            "test files. When done, write to `/tmp/<your-workdir>/SOLUTION.json` "
            "with:\n"
            "```json\n"
            "{\n"
            '  "files_modified": ["src/rate_limit.py"],\n'
            '  "function_signature": "enforce_strict_rate_limit(user_id: str, max_per_min: int) -> None"\n'
            "}\n"
            "```"
        ),
        files_to_modify=["src/rate_limit.py"],
        grader_test_path="tests/_grader/test_T3.py",
        rubric={
            "code_change_correct": {
                "weight": 1.0,
                "description": "Does the function exist with the right signature, use the rl-strict: key prefix, and reject over-limit requests?",
            },
        },
        ground_truth=(
            "Add a function in `src/rate_limit.py` that constructs a Redis key "
            "with prefix `rl-strict:{user_id}:{minute}` (or similar), increments "
            "with the same TTL pattern as the existing RateLimiter, and raises "
            "HTTPException(429) when the count exceeds `max_per_min`."
        ),
        hint_files=["src/rate_limit.py"],
    ),
    "T4": TaskInstance(
        id="small-T4",
        corpus="small",
        task_type="explanation",
        question=(
            "A user reports that their cart has items, but POST /orders/checkout "
            "returns HTTP 400 with the body `{\"detail\": \"cart is empty\"}`. "
            "The user added the items moments before. They are logged in with a "
            "valid session.\n\n"
            "Investigate the codebase. What is the most likely root cause? "
            "Cite specific files and code paths.\n\n"
            "Your final answer should be a JSON object at "
            "`/tmp/<your-workdir>/SOLUTION.json`:\n"
            "```json\n"
            "{\n"
            '  "root_cause": "The most likely cause is ...",\n'
            '  "files_involved": ["src/foo.py", "src/bar.py"],\n'
            '  "explanation": "Detailed walk-through of the failure path..."\n'
            "}\n"
            "```"
        ),
        files_to_modify=[],
        grader_test_path=None,    # subjective only
        rubric={
            "identifies_silent_failure": {
                "weight": 0.4,
                "description": "Does the answer identify that CartStore.get returns an empty Cart when redis returns None, with no warning?",
            },
            "identifies_user_id_mismatch": {
                "weight": 0.4,
                "description": "Does the answer identify the user_id mismatch (cart key from add_item vs read in checkout) as a possible cause?",
            },
            "cites_specific_files": {
                "weight": 0.2,
                "description": "Does the answer cite specific files (src/cart.py, src/api_orders.py, src/api_cart.py, src/deps.py, src/auth.py) and function names?",
            },
        },
        ground_truth=(
            "The root cause is silent failure on the read path combined with a "
            "possible user_id mismatch. `CartStore.get()` in `src/cart.py` returns "
            "an empty Cart when redis.get returns None, with no log. The redis "
            "client may be pointed at a different db (singleton from get_redis() "
            "in src/deps.py), or the user_id in the JWT 'sub' claim may not match "
            "the user_id used in the cart write path (src/api_cart.py add_item uses "
            "current_user.id, src/api_orders.py checkout also uses current_user.id, "
            "but if the user record was re-created with a new UUID, the token's "
            "stale sub leads to a different key)."
        ),
        hint_files=["src/api_cart.py", "src/api_orders.py", "src/cart.py", "src/deps.py", "src/auth.py"],
    ),
}


# ----------------------------------------------------------------------------
# CLI for inspection
# ----------------------------------------------------------------------------

def all_tasks() -> dict[str, TaskInstance]:
    return {**SMALL_TASKS}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: tasks.py <task_id|list>")
        sys.exit(1)
    if sys.argv[1] == "list":
        for k, t in all_tasks().items():
            print(f"  {k}  ({t.task_type})  {t.id}")
    else:
        t = all_tasks()[sys.argv[1]]
        print(json.dumps(asdict(t), indent=2))
