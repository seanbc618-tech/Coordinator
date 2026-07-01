"""Phase 16 red tests: agent capability profiles and persistence.

Owner: Grok (Phase 16 Task 0)
Expected before implementation: agent_capabilities module and migration 026 missing.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
)
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


def _worker_config(*agent_ids: str) -> CoordinatorConfig:
    agents = {
        agent_id: AgentConfig(
            id=agent_id,
            command="true",
            capabilities=["code"],
            max_concurrency=1,
            role="worker",
        )
        for agent_id in agent_ids
    }
    return CoordinatorConfig(
        agents=agents,
        repos={},
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


class AgentCapabilityEnumTests(unittest.TestCase):
    def test_validate_risk_tier_rejects_unknown(self) -> None:
        from local_cli_coordinator.agent_capabilities import validate_risk_tier

        with self.assertRaises(ValueError):
            validate_risk_tier("extreme")

    def test_validate_review_strength_accepts_strong(self) -> None:
        from local_cli_coordinator.agent_capabilities import validate_review_strength

        self.assertEqual(validate_review_strength("strong"), "strong")


class AgentCapabilityMigrationTests(unittest.TestCase):
    def test_migration_026_tables_exist(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        paths = RuntimePaths(tmp / "config", tmp / "data", tmp / "state")
        paths.create()
        conn = connect(paths.database)
        init_db(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        self.assertIn("agent_capability_profiles", tables)
        self.assertIn("agent_benchmark_runs", tables)
        self.assertIn("agent_route_decisions", tables)
        self.assertIn("agent_fallback_edges", tables)
        conn.close()


class AgentCapabilityPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths(
            self.tmp / "config", self.tmp / "data", self.tmp / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.config = _worker_config("alpha", "beta")

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_capability_profiles_syncs_from_config(self) -> None:
        from local_cli_coordinator.agent_capabilities import load_capability_profiles

        profiles = load_capability_profiles(self.conn, self.config, sync=True)
        self.assertEqual(set(profiles), {"alpha", "beta"})
        row = self.conn.execute(
            "select count(*) as cnt from agent_capability_profiles"
        ).fetchone()
        self.assertEqual(int(row["cnt"]), 2)

    def test_upsert_capability_profile_persists_override(self) -> None:
        from local_cli_coordinator.agent_capabilities import (
            profile_from_agent_config,
            upsert_capability_profile,
            get_capability_profile,
        )

        agent = self.config.agents["alpha"]
        profile = profile_from_agent_config(
            agent,
            risk_tier="high",
            review_strength="strong",
            enabled=False,
        )
        upsert_capability_profile(self.conn, profile)
        stored = get_capability_profile(self.conn, agent_id="alpha")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.risk_tier, "high")
        self.assertFalse(stored.enabled)

    def test_disabled_profile_blocks_skill_match(self) -> None:
        from local_cli_coordinator.agent_capabilities import (
            agent_has_skills,
            profile_from_agent_config,
            upsert_capability_profile,
        )

        agent = self.config.agents["alpha"]
        profile = profile_from_agent_config(agent, enabled=False)
        upsert_capability_profile(self.conn, profile)
        self.assertTrue(agent_has_skills(profile, ["code"]))


if __name__ == "__main__":
    unittest.main()