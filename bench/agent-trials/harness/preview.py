#!/usr/bin/env python3
"""
Preview run for PAGED mode — execute tool calls, print the stubs,
let me decide what to recall. No final answer needed.
"""
import sys
sys.path.insert(0, "/Users/chaz/Library/Mobile Documents/com~apple~CloudDocs/Agentic Projects/context-paging")
sys.path.insert(0, "/tmp/paging-test-corpus")

from driver import Trial
import json

def preview(task_id, mode, threshold, plan_no_answer):
    trial = Trial(task_id=task_id, task_prompt="PREVIEW",
                  mode=mode, threshold=threshold, max_calls=40)
    for step in plan_no_answer:
        if step["action"] == "tool":
            r = trial.run_tool(step["tool"], step["args"])
            print(f"  [{step.get('n','?')}] {step['tool']}({json.dumps(step['args'])})")
            if r.get("stashed"):
                print(f"      STASH_ID = {r['stash_id']}")
            print()
    return trial

if __name__ == "__main__":
    plan = [
        {"action": "tool", "tool": "Glob", "args": {"pattern": "src/*.py"}, "n": 1},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/auth.py"}, "n": 2},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/settings.py"}, "n": 3},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/api_users.py"}, "n": 4},
        {"action": "tool", "tool": "Read", "args": {"file_path": "src/main.py"}, "n": 5},
        {"action": "tool", "tool": "Grep", "args": {"pattern": "jwt|JWT|secret|SECRET"}, "n": 6},
    ]
    t = preview("1", "PAGED", 300, plan)
    print()
    print("=== ALL STUBS ===")
    print(t.list_stashes())
