"""Phase 16 red tests: explainable agent routing and bounded fallback.

Owner: Grok (Phase 16 Task 0)
Expected before implementation: agent_router and agent_fallback_graph missing.
"""

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
from local_cli_coordinator.runtime_paths import RuntimePaths
from tests.helpers import init_git_repo


def _config(
    *,
    agents: dict[str, AgentConfig] | None = None,
    repo_allow_push: bool = False,
) -> CoordinatorConfig:
    if agents is None:
        agents = {
            "alpha": AgentConfig(
                id="alpha",
                command="true",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
                fallback_agents=("beta",),
            ),
            "beta": AgentConfig(
                id="beta",
                command="true",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        }
    return CoordinatorConfig(
        agents=agents,
        repos={
            "test-repo": RepoConfig(
                id="test-repo",
                path=Path("/tmp/repo"),
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=repo_allow_push,
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


class AgentRouterScoringTests(unittest.TestCase):
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
            "insert into projects(id, repo_id, canonical_path) "
            "values (?, ?, ?)",
            (self.project_id, "test-repo", "/tmp/repo"),
        )
        self.config = _config()
        from local_cli_coordinator.agent_capabilities import load_capability_profiles

        load_capability_profiles(self.conn, self.config, sync=True)

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_agent_excluded_from_ranking(self) -> None:
        from local_cli_coordinator.agent_capabilities import (
            profile_from_agent_config,
            upsert_capability_profile,
        )
        from local_cli_coordinator.agent_router import rank_agents_for_task

        upsert_capability_profile(
            self.conn,
            profile_from_agent_config(
                self.config.agents["alpha"],
                enabled=False,
            ),
        )
        ranked = rank_agents_for_task(
            self.conn,
            self.config,
            project_id=self.project_id,
            capabilities=["code"],
        )
        self.assertNotIn("alpha", ranked)
        self.assertIn("beta", ranked)

    def test_score_candidates_include_human_readable_reason(self) -> None:
        from local_cli_coordinator.agent_router import score_agent_candidates

        candidates = score_agent_candidates(
            self.conn,
            self.config,
            project_id=self.project_id,
            capabilities=["code"],
            repo_id="test-repo",
        )
        self.assertTrue(candidates)
        self.assertTrue(all(item.reason for item in candidates))

    def test_record_route_decision_persists_candidates(self) -> None:
        from local_cli_coordinator.agent_router import (
            CandidateScore,
            get_route_decision,
            record_route_decision,
        )

        scores = [
            CandidateScore(
                agent_id="alpha",
                score=90.0,
                eligible=True,
                reason="role match",
            )
        ]
        record_route_decision(
            self.conn,
            project_id=self.project_id,
            task_id="task-1",
            selected_agent_id="alpha",
            candidate_scores=scores,
            reason="selected alpha: role match",
        )
        self.conn.commit()
        stored = get_route_decision(
            self.conn, task_id="task-1", project_id=self.project_id
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["selected_agent_id"], "alpha")
        self.assertEqual(len(stored["candidate_scores"]), 1)


class AgentFallbackGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = RuntimePaths(
            self.tmp / "config", self.tmp / "data", self.tmp / "state"
        )
        self.paths.create()
        self.conn = connect(self.paths.database)
        init_db(self.conn)
        self.config = _config()

    def tearDown(self) -> None:
        self.conn.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fallback_respects_max_hops(self) -> None:
        from local_cli_coordinator.agent_fallback_graph import (
            find_fallback_agent,
            record_fallback_edge,
        )

        record_fallback_edge(
            self.conn,
            from_agent_id="alpha",
            to_agent_id="beta",
            max_hops=1,
        )
        first = find_fallback_agent(
            self.conn,
            self.config,
            from_agent_id="alpha",
            required_capabilities=["code"],
        )
        self.assertEqual(first.agent_id, "beta")
        second = find_fallback_agent(
            self.conn,
            self.config,
            from_agent_id="beta",
            required_capabilities=["code"],
            hops_remaining=0,
        )
        self.assertIsNone(second.agent_id)
        self.assertIn("hop limit", second.reason)

    def test_fallback_avoids_cycles(self) -> None:
        from local_cli_coordinator.agent_fallback_graph import (
            find_fallback_agent,
            record_fallback_edge,
        )

        record_fallback_edge(
            self.conn,
            from_agent_id="alpha",
            to_agent_id="beta",
            max_hops=2,
        )
        record_fallback_edge(
            self.conn,
            from_agent_id="beta",
            to_agent_id="alpha",
            max_hops=2,
        )
        result = find_fallback_agent(
            self.conn,
            self.config,
            from_agent_id="alpha",
            required_capabilities=["code"],
        )
        self.assertEqual(result.agent_id, "beta")


if __name__ == "__main__":
    unittest.main()