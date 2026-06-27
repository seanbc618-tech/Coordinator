"""Fake Commander agent for E2E testing.

Reads the repo_id from a file path passed as argv[1], or uses a default.
Task count is controlled by COORDINATOR_FAKE_COMMANDER_TASKS (default 1).
Outputs valid Commander schema JSON to stdout.
"""

import json
import os
import sys
from pathlib import Path

SCHEMA_VERSION = 2

repo_id = "test-repo"
if len(sys.argv) > 1:
    repo_id_file = Path(sys.argv[1])
    if repo_id_file.exists():
        repo_id = repo_id_file.read_text().strip()

task_count = max(1, int(os.environ.get("COORDINATOR_FAKE_COMMANDER_TASKS", "1")))
tasks = [
    {
        "title": f"fake-task-{index}",
        "repo": repo_id,
        "capabilities": ["code"],
        "goal": f"complete slice {index}",
        "acceptance_criteria": [f"slice {index} passes"],
        "verification_commands": ["true"],
        "expected_files": 1,
        "expected_minutes": 5,
        "parent_task_id": None,
        "rationale": f"fake task {index} for testing",
    }
    for index in range(1, task_count + 1)
]

output = {
    "schema_version": SCHEMA_VERSION,
    "intent": "task_request",
    "user_reply": "Fake Commander: plan generated",
    "goal_status": "active",
    "progress_summary": "Fake Commander: plan generated",
    "tasks": tasks,
    "stop_reason": "plan_complete",
}

json.dump(output, sys.stdout)
sys.stdout.write("\n")
