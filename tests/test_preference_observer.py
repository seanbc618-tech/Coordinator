"""Phase 19 tests: preference observation with redaction and scoping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.preference_observer import (
    observe_command_pattern,
    observe_route_override,
    observe_task_approval,
    observe_task_rejection,
)
from local_cli_coordinator.preference_rules import list_observations
from local_cli_coordinator.runtime_paths import RuntimePaths


class PreferenceObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths(
            self.tmp / "config", self.tmp / "data", self.tmp / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.project_a = "proj-a"
        self.project_b = "proj-b"
        self.conn.execute(
            "insert into projects(id, repo_id, canonical_path) values (?, ?, ?)",
            (self.project_a, "repo-a", "/tmp/a"),
        )
        self.conn.execute(
            "insert into projects(id, repo_id, canonical_path) values (?, ?, ?)",
            (self.project_b, "repo-b", "/tmp/b"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_observe_approval_records_observation(self) -> None:
        obs = observe_task_approval(
            self.conn,
            project_id=self.project_a,
            task_id="task-1",
            title="tiny read-only docs fix",
            commit=True,
        )
        self.assertEqual(obs.observation_type, "approval")
        self.assertEqual(obs.subject, "task-1")
        stored = list_observations(self.conn, project_id=self.project_a)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].id, obs.id)

    def test_observe_redacts_secrets_in_evidence(self) -> None:
        obs = observe_task_rejection(
            self.conn,
            project_id=self.project_a,
            task_id="task-secret",
            reason="token=super-secret-value api_key=abc123",
            commit=True,
        )
        self.assertEqual(obs.redaction_status, "redacted")
        self.assertIn("[REDACTED]", obs.evidence["reason"])
        self.assertNotIn("super-secret-value", obs.evidence["reason"])

    def test_observe_command_pattern_scoped_to_project(self) -> None:
        observe_command_pattern(
            self.conn,
            project_id=self.project_a,
            command="/status",
            commit=True,
        )
        observe_command_pattern(
            self.conn,
            project_id=self.project_b,
            command="/tasks",
            commit=True,
        )
        project_a_obs = list_observations(self.conn, project_id=self.project_a)
        project_b_obs = list_observations(self.conn, project_id=self.project_b)
        self.assertEqual(len(project_a_obs), 1)
        self.assertEqual(project_a_obs[0].subject, "/status")
        self.assertEqual(len(project_b_obs), 1)
        self.assertEqual(project_b_obs[0].subject, "/tasks")

    def test_observe_route_override_creates_suggested_rule(self) -> None:
        observe_route_override(
            self.conn,
            project_id=self.project_a,
            task_id="task-1",
            selected_agent_id="grok",
            commit=True,
        )
        observe_route_override(
            self.conn,
            project_id=self.project_a,
            task_id="task-2",
            selected_agent_id="grok",
            commit=True,
        )
        from local_cli_coordinator.preference_rules import list_rules

        rules = list_rules(
            self.conn,
            project_id=self.project_a,
            status="suggested",
        )
        self.assertTrue(rules)
        self.assertEqual(rules[0].rule_type, "agent_choice")
        self.assertEqual(rules[0].status, "suggested")


if __name__ == "__main__":
    unittest.main()