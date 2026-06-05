#!/usr/bin/env python3
"""Preview the stubs for Task 3 files to plan the trial."""
import sys
sys.path.insert(0, "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging")
sys.path.insert(0, "/tmp/paging-test-corpus")
from driver import Trial
import json

plan = [
    {"tool": "Read", "args": {"file_path": "src/api_cart.py"}},
    {"tool": "Read", "args": {"file_path": "src/api_orders.py"}},
    {"tool": "Read", "args": {"file_path": "src/cart.py"}},
    {"tool": "Read", "args": {"file_path": "src/auth.py"}},
    {"tool": "Read", "args": {"file_path": "src/deps.py"}},
    {"tool": "Read", "args": {"file_path": "src/models.py"}},
    {"tool": "Read", "args": {"file_path": "src/orders.py"}},
]

t = Trial("3", "PREVIEW", "PAGED", threshold=300, max_calls=40)
for p in plan:
    r = t.run_tool(p["tool"], p["args"])
    label = f"  {p['tool']}({p['args']['file_path']})"
    if r.get("stashed"):
        label += f"  -> {r['stash_id']}"
    print(label)
print()
print("=== STUBS ===")
print(t.list_stashes())
