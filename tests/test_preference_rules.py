"""Phase 19 tests: preference rule lifecycle and safety constraints."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
    RepoConfig,
)
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.preference_rules import (
    approve_rule,
    create_rule,
    delete_rule,
    export_rules,
    list_active_rules,
    list_rules,
    record_observation,
    reject_rule,
    validate_rule_payload,
)
from local_cli_coordinator.runtime_paths import RuntimePaths


def _config() -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "alpha": AgentConfig(
                id="alpha",
                command="true",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
            "beta": AgentConfig(
                id="beta",
                command="true",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        },
        repos={
            "test-repo": RepoConfig(
                id="test-repo",
                path=Path("/tmp/repo"),
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=False,
                merge_policy="no_push",
                verify_commands=[],
            )
        },
        policy=PolicyConfig(
            require_single_repo=False,
            require_acceptance_criteria=False,
            require_verification_commands=False,
            require_handoff_summary=False,
            max_files_touched=10,
            max_expected_minutes=30,
            max_attempts=2,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
        daemon_policy=DaemonPolicyConfig(),
    )


class PreferenceRulesTests(unittest.TestCase):
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
        self.config = _config()
        from local_cli_coordinator.agent_capabilities import load_capability_profiles

        load_capability_profiles(self.conn, self.config, sync=True)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migration_creates_preference_tables(self) -> None:
        tables = {
            row["name"]
            for row in self.conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertIn("preference_observations", tables)
        self.assertIn("preference_rules", tables)

    def test_create_rule_starts_as_suggested(self) -> None:
        rule = create_rule(
            self.conn,
            scope="project",
            project_id=self.project_a,
            rule_type="agent_choice",
            rule={"preferred_agent_id": "alpha"},
            commit=True,
        )
        self.assertEqual(rule.status, "suggested")

    def test_approve_rule_activates(self) -> None:
        rule = create_rule(
            self.conn,
            scope="project",
            project_id=self.project_a,
            rule_type="agent_choice",
            rule={"preferred_agent_id": "alpha", "score_bonus": 20.0},
            commit=True,
        )
        approved = approve_rule(self.conn, rule_id=rule.id, commit=True)
        self.assertEqual(approved.status, "active")
        active = list_active_rules(self.conn, project_id=self.project_a)
        self.assertEqual(len(active), 1)

    def test_reject_rule_marks_rejected(self) -> None:
        rule = create_rule(
            self.conn,
            scope="project",
            project_id=self.project_a,
            rule_type="task_style",
            rule={"prefer_small_tasks": True},
            commit=True,
        )
        rejected = reject_rule(self.conn, rule_id=rule.id, commit=True)
        self.assertEqual(rejected.status, "rejected")

    def test_delete_rule_soft_deletes(self) -> None:
        rule = create_rule(
            self.conn,
            scope="project",
            project_id=self.project_a,
            rule_type="task_style",
            rule={"reject_vague_tasks": True},
            status="active",
            commit=True,
        )
        deleted = delete_rule(self.conn, rule_id=rule.id, commit=True)
        self.assertEqual(deleted.status, "deleted")
        visible = list_rules(self.conn, project_id=self.project_a)
        self.assertEqual(visible, [])

    def test_forbidden_permission_keys_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_rule_payload({"allow_push": True})
        with self.assertRaises(ValueError):
            create_rule(
                self.conn,
                scope="project",
                project_id=self.project_a,
                rule_type="risk_preference",
                rule={"autonomy_enabled": True},
                commit=True,
            )

    def test_project_scoped_rules_not_active_for_other_project(self) -> None:
        create_rule(
            self.conn,
            scope="project",
            project_id=self.project_a,
            rule_type="agent_choice",
            rule={"preferred_agent_id": "alpha", "score_bonus": 25.0},
            status="active",
            commit=True,
        )
        active_for_b = list_active_rules(self.conn, project_id=self.project_b)
        self.assertEqual(active_for_b, [])

    def test_export_rules_includes_observations(self) -> None:
        record_observation(
            self.conn,
            observation_type="approval",
            subject="task-1",
            evidence={"title": "small task"},
            project_id=self.project_a,
            commit=True,
        )
        payload = export_rules(self.conn, project_id=self.project_a)
        self.assertIn("rules", payload)
        self.assertIn("observations", payload)
        self.assertEqual(len(payload["observations"]), 1)

    def test_active_agent_preference_affects_router_scoring(self) -> None:
        from local_cli_coordinator.agent_router import score_agent_candidates

        create_rule(
            self.conn,
            scope="project",
            project_id=self.project_a,
            rule_type="agent_choice",
            rule={"preferred_agent_id": "beta", "score_bonus": 30.0},
            status="active",
            commit=True,
        )
        candidates = score_agent_candidates(
            self.conn,
            self.config,
            project_id=self.project_a,
            capabilities=["code"],
            repo_id="test-repo",
        )
        beta = next(item for item in candidates if item.agent_id == "beta")
        self.assertIn("rule prefrule-", beta.reason)
        alpha = next(item for item in candidates if item.agent_id == "alpha")
        self.assertNotIn("rule prefrule-", alpha.reason)


if __name__ == "__main__":
    unittest.main()