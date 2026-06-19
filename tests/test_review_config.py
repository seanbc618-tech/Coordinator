import tempfile
import textwrap
import unittest
from pathlib import Path

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    PolicyConfig,
    load_config,
    select_agent_by_role,
)
from local_cli_coordinator.engine import _select_agent


def write_base_config(root: Path, agents_toml: str) -> None:
    (root / "config").mkdir()
    (root / "config" / "agents.toml").write_text(textwrap.dedent(agents_toml).strip())
    (root / "config" / "repos.toml").write_text(textwrap.dedent("""
        [repos.demo]
        path = "/tmp/demo"
        default_branch = "main"
        remote = "origin"
        branch_prefix = "coord/"
        allow_push = false
        merge_policy = "no_push"
        verify_commands = ["python -m unittest"]
    """).strip())
    (root / "config" / "policy.toml").write_text(textwrap.dedent("""
        [task_policy]
        require_single_repo = true
        require_acceptance_criteria = true
        require_verification_commands = true
        require_handoff_summary = true
        max_files_touched = 3
        max_expected_minutes = 30
        max_attempts = 3
        split_if_touches_multiple_subsystems = true
        split_if_research_and_code_are_mixed = true
    """).strip())


class ReviewConfigTests(unittest.TestCase):
    def test_load_config_supports_agent_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_base_config(
                root,
                """
                [agents.worker]
                command = "worker {prompt_path}"
                capabilities = ["code"]
                max_concurrency = 1

                [agents.spec]
                command = "spec {prompt_path}"
                capabilities = ["code"]
                max_concurrency = 1
                role = "spec_reviewer"

                [agents.quality]
                command = "quality {prompt_path}"
                capabilities = ["code"]
                max_concurrency = 1
                role = "quality_reviewer"

                [agents.planner]
                command = "planner {prompt_path}"
                capabilities = ["planner"]
                max_concurrency = 1
                role = "planner"
                """,
            )

            config = load_config(root)

        self.assertEqual(config.agents["worker"].role, "worker")
        self.assertEqual(config.agents["spec"].role, "spec_reviewer")
        self.assertEqual(config.agents["quality"].role, "quality_reviewer")
        self.assertEqual(config.agents["planner"].role, "planner")

    def test_existing_agent_configs_default_to_worker_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_base_config(
                root,
                """
                [agents.codex]
                command = "codex exec {prompt_path}"
                capabilities = ["code", "tests"]
                max_concurrency = 2
                """,
            )

            config = load_config(root)

        self.assertEqual(config.agents["codex"].role, "worker")

    def test_select_agent_can_filter_by_role_and_capabilities(self) -> None:
        config = CoordinatorConfig(
            agents={
                "worker": AgentConfig(
                    id="worker",
                    command="worker",
                    capabilities=["code"],
                    max_concurrency=1,
                    role="worker",
                ),
                "spec": AgentConfig(
                    id="spec",
                    command="spec",
                    capabilities=["code", "tests"],
                    max_concurrency=1,
                    role="spec_reviewer",
                ),
                "quality": AgentConfig(
                    id="quality",
                    command="quality",
                    capabilities=["docs"],
                    max_concurrency=1,
                    role="quality_reviewer",
                ),
            },
            repos={},
            policy=PolicyConfig(
                require_single_repo=True,
                require_acceptance_criteria=True,
                require_verification_commands=True,
                require_handoff_summary=True,
                max_files_touched=3,
                max_expected_minutes=30,
                max_attempts=3,
                split_if_touches_multiple_subsystems=True,
                split_if_research_and_code_are_mixed=True,
            ),
        )

        self.assertEqual(_select_agent(config, ["code"], role="worker").id, "worker")
        self.assertEqual(
            _select_agent(config, ["code"], role="spec_reviewer").id,
            "spec",
        )
        self.assertIsNone(_select_agent(config, ["code"], role="quality_reviewer"))

    def test_load_config_supports_commander_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_base_config(
                root,
                """
                [agents.codex]
                command = "codex exec {prompt_path}"
                capabilities = ["code", "tests", "docs", "research"]
                max_concurrency = 1
                role = "commander"
                """,
            )

            config = load_config(root)

        self.assertEqual(config.agents["codex"].role, "commander")
        self.assertEqual(
            select_agent_by_role(config, "commander").id,
            "codex",
        )

    def test_load_config_rejects_unsupported_agent_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_base_config(
                root,
                """
                [agents.bad]
                command = "bad {prompt_path}"
                capabilities = ["code"]
                max_concurrency = 1
                role = "supervisor"
                """,
            )

            with self.assertRaisesRegex(ValueError, "unsupported role"):
                load_config(root)
