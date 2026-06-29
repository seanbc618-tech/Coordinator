"""Red tests for Phase 7 local agent scorecards.

These tests capture the contract for ``agent_scorecard.py``:
outcome tracking, cooldowns, and read-only routing hints.

Owner: Grok (Phase 7 Task 0)
Expected before implementation: ``ModuleNotFoundError`` for ``agent_scorecard``.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    DaemonPolicyConfig,
    PolicyConfig,
)
from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import inspect_project, register_project
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


def _worker_config(*agent_ids: str) -> CoordinatorConfig:
    agents = {
        agent_id: AgentConfig(
            id=agent_id,
            command=f"{agent_id} ...",
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
            require_single_repo=True,
            require_acceptance_criteria=True,
            require_verification_commands=True,
            require_handoff_summary=True,
            max_files_touched=10,
            max_expected_minutes=30,
            max_attempts=2,
            split_if_touches_multiple_subsystems=False,
            split_if_research_and_code_are_mixed=False,
        ),
        daemon_policy=DaemonPolicyConfig(),
    )


class ScorecardModuleTests(unittest.TestCase):
    """agent_scorecard module exports tracking helpers."""

    def test_scorecard_module_import(self) -> None:
        from local_cli_coordinator.agent_scorecard import (
            AgentScorecard,
            get_agent_scorecard,
            rank_workers_for_capabilities,
            record_agent_outcome,
        )
        self.assertTrue(callable(record_agent_outcome))
        self.assertTrue(callable(get_agent_scorecard))
        self.assertTrue(callable(rank_workers_for_capabilities))
        self.assertTrue(hasattr(AgentScorecard, "agent_id"))


class ScorecardOutcomeTests(unittest.TestCase):
    """Worker outcomes update per-agent scorecards."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_success_increments_successes(self) -> None:
        from local_cli_coordinator.agent_scorecard import (
            get_agent_scorecard,
            record_agent_outcome,
        )

        record_agent_outcome(
            self.conn,
            agent_id="claude",
            role="worker",
            outcome="success",
            runtime_seconds=12.5,
        )
        card = get_agent_scorecard(self.conn, agent_id="claude")
        self.assertEqual(card.successes, 1)
        self.assertEqual(card.failures, 0)
        self.assertEqual(card.avg_runtime_seconds, 12.5)

    def test_record_failure_increments_failures_only_for_that_agent(self) -> None:
        from local_cli_coordinator.agent_scorecard import (
            get_agent_scorecard,
            record_agent_outcome,
        )

        record_agent_outcome(
            self.conn, agent_id="claude", role="worker", outcome="failure"
        )
        record_agent_outcome(
            self.conn, agent_id="grok", role="worker", outcome="success"
        )
        claude = get_agent_scorecard(self.conn, agent_id="claude")
        grok = get_agent_scorecard(self.conn, agent_id="grok")
        self.assertEqual(claude.failures, 1)
        self.assertEqual(grok.failures, 0)
        self.assertEqual(grok.successes, 1)

    def test_cooldown_blocks_only_cooled_agent(self) -> None:
        from local_cli_coordinator.agent_scorecard import (
            get_agent_scorecard,
            is_agent_available,
            record_agent_outcome,
            set_agent_cooldown,
        )

        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        set_agent_cooldown(self.conn, agent_id="claude", cooldown_until=until)
        record_agent_outcome(
            self.conn, agent_id="grok", role="worker", outcome="success"
        )
        self.assertFalse(is_agent_available(self.conn, agent_id="claude"))
        self.assertTrue(is_agent_available(self.conn, agent_id="grok"))
        claude = get_agent_scorecard(self.conn, agent_id="claude")
        self.assertEqual(claude.cooldown_until, until)


class ScorecardRoutingTests(unittest.TestCase):
    """Routing hints prefer capable, non-cooled agents with deterministic order."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        init_git_repo(self.repo)
        self.paths = RuntimePaths(
            self.home / "config", self.home / "data", self.home / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.config = _worker_config("claude", "grok", "codex")

    def tearDown(self) -> None:
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rank_workers_skips_cooled_down_agent(self) -> None:
        from local_cli_coordinator.agent_scorecard import (
            rank_workers_for_capabilities,
            set_agent_cooldown,
        )

        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        set_agent_cooldown(self.conn, agent_id="claude", cooldown_until=until)
        ranked = rank_workers_for_capabilities(
            self.config, self.conn, capabilities=["code"]
        )
        self.assertNotIn("claude", ranked[:1])
        self.assertIn("grok", ranked)

    def test_rank_workers_is_deterministic_for_equal_scores(self) -> None:
        from local_cli_coordinator.agent_scorecard import rank_workers_for_capabilities

        first = rank_workers_for_capabilities(
            self.config, self.conn, capabilities=["code"]
        )
        second = rank_workers_for_capabilities(
            self.config, self.conn, capabilities=["code"]
        )
        self.assertEqual(first, second)

    def test_rank_workers_excludes_incapable_agents(self) -> None:
        from local_cli_coordinator.agent_scorecard import rank_workers_for_capabilities

        config = CoordinatorConfig(
            agents={
                "docs-only": AgentConfig(
                    id="docs-only",
                    command="docs ...",
                    capabilities=["docs"],
                    max_concurrency=1,
                    role="worker",
                ),
                "coder": AgentConfig(
                    id="coder",
                    command="code ...",
                    capabilities=["code"],
                    max_concurrency=1,
                    role="worker",
                ),
            },
            repos={},
            policy=_worker_config().policy,
            daemon_policy=DaemonPolicyConfig(),
        )
        ranked = rank_workers_for_capabilities(
            config, self.conn, capabilities=["code"]
        )
        self.assertEqual(ranked, ["coder"])


if __name__ == "__main__":
    unittest.main()