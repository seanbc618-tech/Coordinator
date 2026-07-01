"""Phase 19 tests: evidence-backed preference suggestions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.preference_observer import (
    observe_command_pattern,
    observe_task_approval,
    observe_task_rejection,
)
from local_cli_coordinator.preference_rules import approve_rule, list_rules
from local_cli_coordinator.preference_suggestions import refresh_suggestions_from_observations
from local_cli_coordinator.runtime_paths import RuntimePaths


class PreferenceSuggestionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths(
            self.tmp / "config", self.tmp / "data", self.tmp / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.project_id = "proj-1"
        self.conn.execute(
            "insert into projects(id, repo_id, canonical_path) values (?, ?, ?)",
            (self.project_id, "repo", "/tmp/repo"),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repeated_small_task_approvals_suggest_task_style(self) -> None:
        observe_task_approval(
            self.conn,
            project_id=self.project_id,
            task_id="t1",
            title="tiny read-only docs tweak",
            commit=False,
        )
        observe_task_approval(
            self.conn,
            project_id=self.project_id,
            task_id="t2",
            title="small docs update",
            commit=True,
        )
        rules = list_rules(
            self.conn,
            project_id=self.project_id,
            status="suggested",
        )
        self.assertTrue(any(rule.rule_type == "task_style" for rule in rules))

    def test_suggested_rules_inactive_until_approved(self) -> None:
        observe_task_rejection(
            self.conn,
            project_id=self.project_id,
            task_id="t1",
            reason="too vague, maybe investigate",
            commit=False,
        )
        observe_task_rejection(
            self.conn,
            project_id=self.project_id,
            task_id="t2",
            reason="broad rewrite request",
            commit=True,
        )
        suggested = list_rules(
            self.conn,
            project_id=self.project_id,
            status="suggested",
        )
        self.assertTrue(suggested)
        for rule in suggested:
            self.assertEqual(rule.status, "suggested")
        approved = approve_rule(self.conn, rule_id=suggested[0].id, commit=True)
        self.assertEqual(approved.status, "active")

    def test_evidence_ids_linked_to_suggestions(self) -> None:
        from local_cli_coordinator.preference_rules import record_observation

        obs1 = record_observation(
            self.conn,
            observation_type="command",
            subject="/status",
            evidence={"command": "/status"},
            project_id=self.project_id,
            commit=False,
        )
        obs2 = record_observation(
            self.conn,
            observation_type="command",
            subject="/status",
            evidence={"command": "/status"},
            project_id=self.project_id,
            commit=False,
        )
        created = refresh_suggestions_from_observations(
            self.conn,
            project_id=self.project_id,
            commit=True,
        )
        self.assertTrue(created)
        rule = created[0]
        self.assertIn(obs1.id, rule.evidence_ids)
        self.assertIn(obs2.id, rule.evidence_ids)


if __name__ == "__main__":
    unittest.main()