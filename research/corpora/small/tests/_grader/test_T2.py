"""
Grader for Task T2 (refactor: wire RateLimiter into get_current_user at 30 req/min).

Objective binary check via source inspection:
  1. deps.py imports or constructs a RateLimiter
  2. deps.py has a call that invokes the limiter (e.g. limiter.check(...))
  3. The configured limit is 30 (not 60)

Run with: cd <project_root> && python -m pytest tests/_grader/test_T2.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
DEPS_PATH = PROJECT_ROOT / "src" / "deps.py"


def _read_deps_source() -> str:
    return DEPS_PATH.read_text()


def test_deps_imports_rate_limiter():
    """deps.py must import the RateLimiter class."""
    src = _read_deps_source()
    patterns = [
        re.compile(r"from\s+\.rate_limit\s+import\s+RateLimiter"),
        re.compile(r"from\s+\.+rate_limit\s+import\s+RateLimiter"),
        re.compile(r"import\s+.*RateLimiter"),
    ]
    for pat in patterns:
        if pat.search(src):
            return
    raise AssertionError(
        "deps.py does not import the RateLimiter class.\n\n"
        "Add: `from .rate_limit import RateLimiter` at the top of deps.py"
    )


def test_deps_calls_limiter_check():
    """deps.py must actually invoke the rate limiter check."""
    src = _read_deps_source()
    # Look for any call to .check( on a rate-limiter-like object
    patterns = [
        re.compile(r"\.check\s*\("),
        re.compile(r"limiter\.check"),
        re.compile(r"rate_limiter\.check"),
    ]
    for pat in patterns:
        if pat.search(src):
            return
    raise AssertionError(
        "deps.py does not appear to call .check() on a rate limiter.\n\n"
        "Inside get_current_user (or in a dependency it depends on), "
        "call limiter.check(user.id) to enforce the rate limit."
    )


def test_deps_uses_30_per_minute():
    """The configured limit must be 30, not the default 60."""
    src = _read_deps_source()
    # Look for explicit 30 in the rate limiter context
    # The most common patterns:
    # - limit_per_min=30
    # - 30 req/min
    # - 30 per minute
    found_30 = any([
        "limit_per_min=30" in src,
        "limit_per_min = 30" in src,
        "rate_limit_per_min=30" in src,
        "30 req" in src,
        "30 per" in src,
    ])
    assert found_30, (
        "deps.py does not configure the rate limit to 30 req/min.\n\n"
        "The task requires a strict 30 requests/minute limit. "
        "Pass 30 as the limit_per_min argument to RateLimiter, "
        "or set RATE_LIMIT_PER_MIN=30 in load_settings()."
    )


def test_get_current_user_signature_includes_limiter():
    """get_current_user should accept the rate limiter as a dependency."""
    src = _read_deps_source()
    # The simplest signal: limiter is in the get_current_user parameter list
    # Look for "limiter" or "rate_limiter" in the def line of get_current_user
    sig_pattern = re.compile(
        r"def\s+get_current_user\s*\([^)]*(limiter|rate_limiter)[^)]*\)",
        re.DOTALL,
    )
    if not sig_pattern.search(src):
        # Fallback: check that limiter is a module-level variable or callable
        # that get_current_user could use
        alt_patterns = [
            re.compile(r"def\s+get_current_user[\s\S]{0,2000}limiter"),
            re.compile(r"def\s+get_current_user[\s\S]{0,2000}rate_limiter"),
        ]
        for p in alt_patterns:
            if p.search(src):
                return
        raise AssertionError(
            "get_current_user does not appear to receive a rate limiter.\n\n"
            "Add a parameter `limiter: RateLimiter = Depends(get_rate_limiter)` "
            "(or similar) to the get_current_user signature, and call "
            "limiter.check(user.id) before returning the user."
        )
