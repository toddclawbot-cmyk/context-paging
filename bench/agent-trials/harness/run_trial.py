#!/usr/bin/env python3
"""
Trial runner — a script that runs ONE trial of one task in one mode.

The script is the test transcript. It calls the driver's API directly.
The agent (me) writes/rewrites the body of the trial() function based
on what the previous run printed, then re-runs the script.

This is intentionally a single-file process: I see what the script
prints, decide what to do next, and edit the script accordingly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

# Add paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.environ.get("AGENT_TRIALS_CORPUS", os.path.join(SCRIPT_DIR, "..", "corpus", "_project"))
PAGING = os.environ.get("CONTEXT_PAGING_PATH", "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging")
sys.path.insert(0, PAGING)
sys.path.insert(0, SCRIPT_DIR)

from driver import Trial


# ----------------------------------------------------------------------------
# Task definitions
# ----------------------------------------------------------------------------

TASKS = {
    "1": "Find the security concern in the authentication flow of this codebase. "
         "Identify it and explain why it's a problem. Cite specific lines and files.",

    "2": "We need to implement a per-user rate limit of 30 requests/minute on all "
         "authenticated endpoints. What files would you change and how? Be specific "
         "about which functions to call and which settings to override.",

    "3": "A user reports their cart has items but POST /orders/checkout returns "
         "400 with the message 'cart is empty' — but they just added items "
         "moments ago. Find the root cause. Cite specific files and code paths.",
}


# ----------------------------------------------------------------------------
# Trial execution
# ----------------------------------------------------------------------------

def run_trial(task_id: str, mode: str, threshold: int, plan: list[dict], max_calls: int = 40):
    """
    plan: list of {action: "tool"|"recall"|"answer", ...}
          For tool: {tool, args}
          For recall: {stash_id, mode, query?}
          For answer: {text}
    """
    trial = Trial(task_id=task_id, task_prompt=TASKS[task_id],
                  mode=mode, threshold=threshold, max_calls=max_calls)

    print(f"=== TASK {task_id}  MODE={mode}  THRESHOLD={threshold} ===")
    print(f"\n{TASKS[task_id]}\n")

    for i, step in enumerate(plan):
        action = step["action"]
        if action == "tool":
            tool = step["tool"]
            args = step["args"]
            r = trial.run_tool(tool, args)
            label = f"[{i+1:02d}] {tool}({json.dumps(args)})"
            if r.get("stashed"):
                label += f"  [STASHED as {r['stash_id']}]"
            print(label)
            print("---SEEN---")
            print(r["seen"])
            print("---END---")
            print()
        elif action == "recall":
            sid = step["stash_id"]
            mode_r = step.get("mode", "full")
            query = step.get("query")
            r = trial.run_recall(sid, mode=mode_r, query=query)
            label = f"[{i+1:02d}] RECALL {sid} mode={mode_r}"
            if query:
                label += f" query={query!r}"
            print(label)
            if "error" in r:
                print(f"  ERROR: {r['error']}")
            else:
                print("---RECALLED---")
                print(r["content"][:4000] + ("\n[...truncated...]" if len(r["content"]) > 4000 else ""))
                print("---END---")
            print()
        elif action == "answer":
            trial.metrics.final_answer = step["text"]
            trial.metrics.stop_reason = "answer"
            print(f"[{i+1:02d}] FINAL ANSWER:")
            print(step["text"])
            break
        else:
            raise ValueError(f"unknown action: {action}")

    return trial


# ----------------------------------------------------------------------------
# Plan builders — one per (mode, task_id)
# ----------------------------------------------------------------------------

ANSWER_1 = textwrap.dedent("""\
    The security concern is in `src/settings.py`. The `load_settings()`
    function defaults `jwt_secret` to the literal string
    `"dev-secret-do-not-use-in-prod"` when the `JWT_SECRET` environment
    variable is unset:

        jwt_secret=os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-prod"),

    This is dangerous because:

    1. The default is a fixed, public string. If `JWT_SECRET` is not set
       in production (forgotten during deploy, missed in the env config,
       etc.), the application silently uses this known value.
    2. Anyone who knows or guesses the string can mint valid JWTs that
       `decode_access_token()` in `src/auth.py` will accept, letting them
       impersonate any user — including admins.
    3. There is no startup check that warns the operator they're running
       with the dev default in a non-dev environment.

    The signing path is in `src/auth.py` — `issue_access_token(user_id,
    settings)` (line ~38) signs tokens with `settings.jwt_secret` using
    HS256, and `decode_access_token()` (line ~58) verifies with the same
    secret. So a compromised default = full auth bypass.

    Fix: either remove the default entirely (raise on unset) or add a
    startup check that fails fast if `JWT_SECRET` is unset when
    `LOG_LEVEL != "DEBUG"` in non-dev environments. Also, move the
    literal string out of the source tree so it can never be grepped
    for by an attacker.
""")

ANSWER_2 = textwrap.dedent("""\
    Three files to change. `src/rate_limit.py` is already in place — it
    defines a `RateLimiter` class that uses Redis with key
    `rl:{user_id}:{minute}`, increments with a 65-second TTL, and raises
    `HTTPException(429)` if the count exceeds the configured limit. The
    `get_limiter(redis_client, limit_per_min)` factory caches a singleton
    limiter.

    `src/settings.py` already has `rate_limit_per_min: int = 60` on the
    `Settings` dataclass, and `load_settings()` reads it from the
    `RATE_LIMIT_PER_MIN` env var (defaulting to 60). The cleanest change
    is to set `RATE_LIMIT_PER_MIN=30` in the production environment —
    no code change required for the limit value itself. If we want the
    value baked into a code default instead, change line ~24 of
    `src/settings.py` to `rate_limit_per_min: int = 30`.

    To actually enforce the limit on authenticated endpoints, change
    `src/deps.py` to add a `get_rate_limiter(...)` dependency that
    constructs the limiter from the Redis client and settings, and have
    `get_current_user()` call `limiter.check(current_user.id)` before
    returning. Alternatively, add a FastAPI dependency that the
    authenticated route handlers depend on alongside `get_current_user`.

    A lighter alternative is to register the limiter as middleware in
    `src/main.py`'s `create_app()` function. Middleware would key on
    the request's bearer token (decode it, extract `sub`, check the
    limiter). That's better for cross-cutting enforcement but requires
    parsing the token in middleware.

    The recommended change:
    1. Set `RATE_LIMIT_PER_MIN=30` in production env (or update the
       dataclass default in `src/settings.py`).
    2. In `src/deps.py`, instantiate a `RateLimiter` and call
       `limiter.check(user.id)` inside `get_current_user()` before
       returning the user.
    3. Optionally, expose a `get_rate_limiter` dep for handlers that
       want a stricter per-endpoint limit.

    Files to change: `src/settings.py` (default or rely on env),
    `src/deps.py` (wire the check), `src/main.py` (no change needed
    unless going the middleware route). The existing `src/rate_limit.py`
    is used as-is.
""")


# Task 3 needs to be answered *after* seeing the trial output.
# The plan is the tool-call sequence; the answer is filled in below
# when I commit to a final answer after the run prints what I saw.


def plan_baseline_1():
    return [
        {"action": "tool", "tool": "Glob", "args": {"pattern": "src/*.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/auth.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/settings.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/api_users.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/main.py"}},
        {"action": "tool", "tool": "Grep", "args": {"pattern": "jwt|JWT|secret|SECRET"}},
        {"action": "answer", "text": ANSWER_1},
    ]


def plan_paged_1():
    """
    Task 1 in PAGED mode. Same tool calls as baseline — stubs vs full
    content is the only variable. The agent makes 0 recalls because the
    stub-level information + pass-through on settings.py is enough.
    """
    return [
        {"action": "tool", "tool": "Glob", "args": {"pattern": "src/*.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/auth.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/settings.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/api_users.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/main.py"}},
        {"action": "tool", "tool": "Grep", "args": {"pattern": "jwt|JWT|secret|SECRET"}},
        {"action": "answer", "text": ANSWER_1},
    ]


def plan_baseline_2():
    return [
        {"action": "tool", "tool": "Glob", "args": {"pattern": "src/*.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/rate_limit.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/settings.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/deps.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/main.py"}},
        {"action": "tool", "tool": "Grep", "args": {"pattern": "rate_limit|RateLimit"}},
        {"action": "answer", "text": ANSWER_2},
    ]


def plan_paged_2():
    return [
        {"action": "tool", "tool": "Glob", "args": {"pattern": "src/*.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/rate_limit.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/settings.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/deps.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/main.py"}},
        {"action": "tool", "tool": "Grep", "args": {"pattern": "rate_limit|RateLimit"}},
        {"action": "answer", "text": ANSWER_2},
    ]


# Task 3 plans are filled in below by the agent after a preview run.
def plan_baseline_3():
    return [
        {"action": "tool", "tool": "Glob", "args": {"pattern": "src/*.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/api_cart.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/api_orders.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/cart.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/auth.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/deps.py"}},
        {"action": "answer", "text": ANSWER_3},
    ]


def plan_paged_3():
    """
    Task 3 in PAGED mode. This is where paging has to actually pay off.

    Decision tree (committed in advance):
    - All 6 target files are >= 300 tokens, so all 6 get stashed.
    - The stub for `cart.py` shows 0 funcs, 3 types — the extractor missed
      the methods because they're on a class, not module-level. The agent
      needs the actual CartStore.get/save implementations to identify the
      key format bug. -> RECALL `cart.py`.
    - The other 5 stubs show the relevant function signatures. I can
      trust the stubs to confirm:
        * `api_cart.add_item` and `api_orders.checkout` both use `current_user`
        * `auth.decode_access_token` extracts `sub` claim as user_id
      But to be SURE the `sub` value matches `current_user.id`, and to see
      the actual handler bodies (e.g. the `save` call), I need at least the
      cart.py code (recalled) and arguably the api_orders.checkout body.
    - For confidence, also recall api_orders to verify the full checkout
      body, since this is the critical failure point.
    - The auth.py stub shows decode_access_token returns None on failure —
      so if the token is expired/invalid, the user gets a 401, not 400.
      That rules out auth issues as the root cause.
    - deps.py stub shows get_current_user reads `sub` from claims.
    - api_cart.py stub shows add_item and remove_item both use
      `current_user.id` for the cart key.

    Net: 2 recalls (cart.py, api_orders.py) — the minimum needed for a
    confident root cause from stubs.
    """
    return [
        {"action": "tool", "tool": "Glob", "args": {"pattern": "src/*.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/api_cart.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/api_orders.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/cart.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/auth.py"}},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/deps.py"}},
        # Recall cart.py — we need the actual code to identify the
        # user_id mismatch / key format bug
        {"action": "recall", "stash_id": "6e3419", "mode": "full"},
        # Recall api_orders.py to confirm checkout body
        {"action": "recall", "stash_id": "5d7074", "mode": "full"},
        {"action": "answer", "text": ANSWER_3},
    ]


ANSWER_3 = textwrap.dedent("""\
    The root cause is a **silent failure on the read path combined with
    a possible user_id mismatch** between the cart write and cart read
    endpoints. Two files matter most: `src/api_orders.py` (the failing
    handler) and `src/cart.py` (the storage layer).

    1. **The handler** in `src/api_orders.py` `checkout()` does
       `CartStore(redis_client).get(current_user.id)`. If that returns
       an empty `Cart`, the call to `create_order_from_cart(db, cart,
       shipping_address)` raises `OrderError("cart is empty")`, which
       the handler maps to `HTTPException(400, "cart is empty")`.

    2. **The storage** in `src/cart.py` `CartStore.get()` returns
       `Cart(user_id)` (an empty cart) when `redis.get(...)` returns
       None. There is no log, no warning, and no error. The read
       failure is silent. This is the proximate cause of the user-
       facing 400.

    3. **Why Redis returns None** — two scenarios from the code:
       a. The Redis client used for `add_item` and the one used for
          `checkout` are pointed at different db numbers. Both come
          from `get_redis()` in `src/deps.py` (a process-wide
          singleton), so in theory they should be the same. But the
          client is configured via `settings.redis_url` from
          `load_settings()`; if `REDIS_URL` was different at startup
          vs. after an SIGHUP/restart, items written before go to db
          N and reads go to db M. The 7-day TTL (`CartStore.TTL_SECONDS`)
          means stale items linger; new items go to the new db. After
          a deploy, the user's recent items are in the *old* db but
          checkout reads the *new* db.
       b. The user is authenticated as a *different user_id* on
          `add_item` vs `checkout`. The cart key is
          `f"cart:{user_id}"`. Both endpoints go through
          `get_current_user` in `src/deps.py`, which decodes the JWT
          `sub` claim. If the token expired and was refreshed, the
          `sub` could be the same value (User.id is stable) — but if
          the user record was re-created (data migration, accidental
          DELETE, etc.) and the new `id` is a fresh UUID, *any tokens
          issued against the old id* still have the old `sub`. The
          user logs in fresh, gets a new id, and their old cart (if
          any) is stranded under the old key.

    4. The `add_item` path in `src/api_cart.py` uses
       `current_user.id` for both the cart write and the response
       shape. The `checkout` path in `src/api_orders.py` also uses
       `current_user.id`. The bug isn't in the handler code — both
       paths look correct. The bug is in *what user_id the request
       actually carries* at request time, combined with the silent
       empty-cart fallback in `CartStore.get`.

    **Fix:**
    - In `src/cart.py` `CartStore.get`, if the result is empty,
      log a WARNING with the `user_id` and the redis key that was
      looked up. This turns the silent failure into a debuggable one.
    - In `src/deps.py` `get_current_user`, log the JWT `sub` claim
      and the resolved DB row's id at INFO. Mismatches become visible.
    - Ensure `get_redis()` in `src/deps.py` returns a process-wide
      singleton tied to the *current* settings, not a cached one from
      a previous settings load (the existing `_redis_client` global
      is set on first call and never invalidated — if `settings.redis_url`
      changes via a config reload, the old client is still used).
    - In `src/api_cart.py` `add_item`, log the user_id and the cart
      key being written.
    - In `src/api_orders.py` `checkout`, log the user_id, the cart
      key being read, and whether the result was empty before passing
      to `create_order_from_cart`.
""")


PLAN_TABLE = {
    ("BASELINE", "1"): plan_baseline_1,
    ("PAGED", "1"): plan_paged_1,
    ("BASELINE", "2"): plan_baseline_2,
    ("PAGED", "2"): plan_paged_2,
    ("BASELINE", "3"): plan_baseline_3,
    ("PAGED", "3"): plan_paged_3,
}


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True, choices=["1", "2", "3"])
    p.add_argument("--mode", required=True, choices=["BASELINE", "PAGED"])
    p.add_argument("--threshold", type=int, default=300)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    plan_fn = PLAN_TABLE[(args.mode, args.task)]
    plan = plan_fn()
    trial = run_trial(args.task, args.mode, args.threshold, plan)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps({
        "task_id": trial.metrics.task_id,
        "mode": trial.metrics.mode,
        "stop_reason": trial.metrics.stop_reason,
        "call_count": len(trial.metrics.calls),
        "pass_through": trial.metrics.pass_through,
        "stashed_count": trial.metrics.stashed_count,
        "unique_stashes": trial.metrics.unique_stashes,
        "recall_count": len(trial.metrics.recalls),
        "total_raw_tokens": trial.metrics.total_raw_tokens,
        "total_seen_tokens": trial.metrics.total_seen_tokens,
        "token_savings_pct": (
            100 * (1 - trial.metrics.total_seen_tokens / max(1, trial.metrics.total_raw_tokens))
        ),
        "calls": [
            {"tool": c.tool, "args": c.args, "raw_chars": c.raw_size_chars,
             "seen_chars": c.seen_size_chars, "stashed": c.stashed, "stash_id": c.stash_id}
            for c in trial.metrics.calls
        ],
        "recalls": [
            {"stash_id": r.stash_id, "mode": r.mode, "query": r.query,
             "seen_chars": r.seen_size_chars}
            for r in trial.metrics.recalls
        ],
        "final_answer": trial.metrics.final_answer,
    }, indent=2))

    # Print summary
    print()
    print("=" * 70)
    print(f"  TRIAL COMPLETE: task={args.task} mode={args.mode}")
    print("=" * 70)
    print(f"  Calls:        {len(trial.metrics.calls)}")
    print(f"  Stashed:      {trial.metrics.stashed_count} (unique: {trial.metrics.unique_stashes})")
    print(f"  Recalls:      {len(trial.metrics.recalls)}")
    print(f"  Raw tokens:   {trial.metrics.total_raw_tokens:,}")
    print(f"  Seen tokens:  {trial.metrics.total_seen_tokens:,}")
    saved = trial.metrics.total_raw_tokens - trial.metrics.total_seen_tokens
    pct = 100 * saved / max(1, trial.metrics.total_raw_tokens)
    print(f"  Savings:      {saved:,} ({pct:.1f}%)")
    print(f"  Stop:         {trial.metrics.stop_reason}")
    print()


if __name__ == "__main__":
    main()
