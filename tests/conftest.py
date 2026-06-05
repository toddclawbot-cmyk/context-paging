"""Shared pytest fixtures for the context-paging test suite."""

import os
import shutil
import tempfile

import pytest

from src.store import StashStore
from src.wrapper import ContextPager, InterceptorConfig


@pytest.fixture
def tmp_store_root():
    """A clean temp dir for a stash store."""
    root = tempfile.mkdtemp(prefix="cp-test-")
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def store(tmp_store_root):
    """A fresh StashStore."""
    return StashStore(tmp_store_root)


@pytest.fixture
def pager(tmp_store_root):
    """A ContextPager with a low threshold for easy testing."""
    config = InterceptorConfig(threshold_tokens=100)
    return ContextPager(tmp_store_root, config=config)


@pytest.fixture
def large_code():
    """A code file large enough to trigger stashing."""
    return '''"""JWT auth module for the API."""
import jwt
import redis
from typing import Optional

# Token blocklist key prefix
BLOCKLIST_PREFIX = "auth:blocklist:"

class Claims:
    """JWT claims structure."""
    def __init__(self, sub: str, exp: int, iat: int):
        self.sub = sub
        self.exp = exp
        self.iat = iat


def verify_token(token: str) -> tuple[Optional[Claims], Optional[str]]:
    """Verify a JWT and return the claims or an error message."""
    try:
        payload = jwt.decode(token, verify_exp=True)
        return Claims(payload["sub"], payload["exp"], payload["iat"]), None
    except jwt.ExpiredSignatureError:
        return None, "expired"
    except jwt.InvalidTokenError as e:
        return None, str(e)


def rotate_refresh(token: str) -> tuple[Optional[str], Optional[str]]:
    """Rotate a refresh token. Returns (new_token, error)."""
    claims, err = verify_token(token)
    if err:
        return None, err
    new = jwt.encode({"sub": claims.sub, "exp": claims.exp + 3600})
    return new, None


def parse_header(req) -> tuple[Optional[str], Optional[str]]:
    """Extract bearer token from request headers."""
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, "no bearer token"
    return auth[7:], None


def is_revoked(token: str) -> bool:
    """Check if a token is in the Redis blocklist."""
    r = redis.Redis()
    return r.exists(BLOCKLIST_PREFIX + token) > 0
'''


@pytest.fixture
def large_markdown():
    """A markdown file large enough to trigger stashing."""
    return '''# Project Onyx: Architecture Overview

## 1. Executive Summary

Onyx is a distributed task queue with strong consistency guarantees.
Built on Raft consensus, it processes 50K jobs/sec at p99 < 10ms.

## 2. Components

### 2.1 Scheduler

The scheduler assigns jobs to workers using a consistent-hash ring.
It maintains in-memory state for assignment but persists snapshots to
RocksDB every 30 seconds for crash recovery.

### 2.2 Workers

Workers are stateless processes that pull work from the scheduler.
They report heartbeats every 5 seconds; missed heartbeats trigger
reassignment after 30 seconds.

### 2.3 Storage Backend

Jobs and their results are stored in PostgreSQL with a Redis cache
layer for hot paths. State transitions are journaled for replay.

## 3. Failure Modes

### 3.1 Scheduler Crash

On crash, the new leader replays the WAL to reconstruct state.
Expected recovery time: < 5 seconds.

### 3.2 Worker Crash

Detected via missed heartbeats. In-flight jobs are reassigned.
At-least-once delivery means clients must handle duplicate results.

### 3.3 Network Partition

Raft majority quorum means the system stays available as long as
a majority of schedulers can communicate. Read-only mode kicks in
for clients during split-brain scenarios.

## 4. Deployment

Onyx ships as a Helm chart. Default deployment is 3 schedulers +
N workers, where N scales with job volume. Production deployments
should run on dedicated nodes with NVMe storage.

## 5. Observability

Prometheus metrics for all components. Distributed tracing via
OpenTelemetry. Structured logs ship to Loki by default.
'''


@pytest.fixture
def large_grep():
    """A grep result with many matches."""
    lines = []
    for i in range(50):
        lines.append(f"src/auth/jwt.py:{i+10}:def handler_{i}(req):")
        lines.append(f"src/api/middleware.py:{i+20}:    auth = handler_{i}(req)")
    return "\n".join(lines)
