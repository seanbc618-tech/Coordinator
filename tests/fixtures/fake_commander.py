"""Fake Commander agent for E2E testing.

Reads the repo_id from a file path passed as argv[1], or uses a default.
Outputs valid Commander schema JSON to stdout.
"""

import json
import sys
from pathlib import Path

SCHEMA_VERSION = 2

repo_id = "test-repo"
if len(sys.argv) > 1:
    repo_id_file = Path(sys.argv[1])
    if repo_id_file.exists():
        repo_id = repo_id_file.read_text().strip()

output = {
    "schema_version": SCHEMA_VERSION,
    "intent": "task_request",
    "user_reply": "Fake Commander: plan generated",
    "goal_status": "active",
    "progress_summary": "Fake Commander: plan generated",
    "tasks": [
        {
            "title": "fake-task",
            "repo": repo_id,
            "capabilities": ["code"],
            "goal": "complete the work",
            "acceptance_criteria": ["pass"],
            "verification_commands": ["true"],
            "expected_files": 1,
            "expected_minutes": 5,
            "parent_task_id": None,
            "rationale": "fake task for testing",
        }
    ],
    "stop_reason": "plan_complete",
}

json.dump(output, sys.stdout)
sys.stdout.write("\n")
