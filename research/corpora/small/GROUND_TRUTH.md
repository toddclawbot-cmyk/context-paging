# Ground Truth — MyShop Corpus

Three tasks with verifiable answers. Each requires reading >1 file.

## Task 1 — Code review: find the security bug

**Question:** There's a security concern in the authentication flow.
Identify it and explain why it's a problem.

**Answer (must include all of):**
- The default `jwt_secret` in `settings.py` is `"dev-secret-do-not-use-in-prod"`
- This is a hardcoded fallback that ships to production if `JWT_SECRET` env var is unset
- An attacker who knows this string can forge valid JWTs and impersonate any user
- The fix: remove the default, or fail-fast at startup if `JWT_SECRET` is missing in prod
- **File(s) needing cross-reference:** `src/settings.py` (line with `jwt_secret=...`)
  and `src/auth.py` (`issue_access_token` uses settings.jwt_secret)

## Task 2 — Multi-file feature: rate limiting

**Question:** Implement a per-user rate limit of 30 requests/minute on all
authenticated endpoints. What files would you change and how?

**Answer (must include all of):**
- Add a FastAPI dependency or middleware that calls `RateLimiter.check(user.id)`
- `src/rate_limit.py` already defines `RateLimiter` keyed on `rl:{user_id}:{minute}`
- Wire it into `src/deps.py` (e.g. a new `get_rate_limiter` dep) or apply it
  as middleware in `src/main.py`'s `create_app()`
- The `Settings.rate_limit_per_min` field already exists; default is 60,
  needs override to 30
- **File(s) needing cross-reference:** `src/rate_limit.py`,
  `src/deps.py`, `src/main.py`, `src/settings.py`

## Task 3 — Bug investigation: checkout fails silently

**Question:** A user reports their cart has items but POST /orders/checkout
returns 400 with the message "cart is empty" — but they just added items.
Find the root cause.

**Answer (must include all of):**
- The handler in `src/api_orders.py` calls `CartStore(redis_client).get(current_user.id)`
- The `CartStore` constructor in `src/cart.py` does `redis.from_url(...)` only
  inside `get_redis()` in `deps.py` — but the call passes the *redis_client* dependency
- **Real cause:** `Cart.get()` returns an empty Cart when `redis.get(...)` returns None
- This happens when the redis client uses a *different db number* than the one
  items were written to, OR when redis wasn't flushed between deploys
- More subtly: the `add_item` endpoint calls `cart.save(cart)` which `setex`s with
  TTL 7 days — but if the user is logged in with a *different* user_id (token
  `sub` mismatch), the cart key is wrong
- The fix: log the user_id and the redis key, and ensure `current_user.id` in
  the checkout matches the `user_id` in the cart write path
- **File(s) needing cross-reference:** `src/api_cart.py` (add_item uses
  `current_user.id`), `src/api_orders.py` (checkout uses `current_user.id`),
  `src/cart.py` (CartStore key format), `src/auth.py` (JWT `sub` claim)
