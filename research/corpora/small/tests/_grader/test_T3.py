"""
Grader for Task T3 (feature-add: enforce_strict_rate_limit function).

Objective binary check via source inspection.
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
RATE_LIMIT_PATH = PROJECT_ROOT / "src" / "rate_limit.py"


def _read_source() -> str:
    return RATE_LIMIT_PATH.read_text()


def test_function_exists_with_correct_signature():
    src = _read_source()
    pattern = re.compile(
        r"def\s+enforce_strict_rate_limit\s*\(\s*user_id\s*[:,]\s*str[^)]*max_per_min\s*[:,]\s*int[^)]*\)\s*->\s*None"
    )
    if pattern.search(src):
        return
    lenient = re.compile(
        r"def\s+enforce_strict_rate_limit\s*\([^)]*user_id[^)]*max_per_min[^)]*\)"
    )
    if lenient.search(src):
        return
    raise AssertionError(
        "rate_limit.py does not define enforce_strict_rate_limit(user_id, max_per_min) -> None.\n\n"
        "Add: def enforce_strict_rate_limit(user_id: str, max_per_min: int) -> None"
    )


def test_uses_separate_redis_key_prefix():
    src = _read_source()
    candidate_prefixes = [
        "rl-strict:",
        "rl_strict:",
        "rlstrict:",
        "rl-strict-",
    ]
    for prefix in candidate_prefixes:
        if prefix in src:
            return
    # The task says use a "different" prefix; "rl-strict" is the canonical
    # example. Allow a function-scoped variable too.
    fn_match = re.search(
        r"def\s+enforce_strict_rate_limit[\s\S]+?(?=\ndef |\nclass |\Z)",
        src
    )
    if fn_match:
        body = fn_match.group(0)
        if re.search(r"[a-z]+-?[a-z]*strict[a-z:-]*", body):
            return
    raise AssertionError(
        "rate_limit.py does not use a separate Redis key prefix for the strict limiter.\n\n"
        "The function should construct keys like rl-strict:{user_id}:{minute} "
        "so the strict limit does not share state with the global RateLimiter."
    )


def test_raises_on_excess():
    src = _read_source()
    fn_match = re.search(
        r"def\s+enforce_strict_rate_limit\s*\([^)]*\)\s*->\s*None\s*:\s*\n(.*?)(?=\ndef |\nclass |\Z)",
        src, re.DOTALL
    )
    if fn_match:
        body = fn_match.group(1)
        if "HTTPException" in body and "429" in body:
            return
    # No fallback — if the function doesn't exist, we can't check it raises.
    raise AssertionError(
        "enforce_strict_rate_limit does not raise HTTPException(429) on excess.\n\n"
        "After incrementing the count, check if it exceeds max_per_min and "
        "raise HTTPException(status_code=429, detail=\"rate limit exceeded\")."
    )
