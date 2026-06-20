"""Tests for fallback agent selection."""

from unittest import TestCase

from local_cli_coordinator.config import (
    AgentConfig,
    CoordinatorConfig,
    PolicyConfig,
    RepoConfig,
    DaemonPolicyConfig,
    select_fallback_agent,
)


def _make_config(agents: dict[str, AgentConfig]) -> CoordinatorConfig:
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
    )


class SelectFallbackAgentTest(TestCase):
    def test_returns_first_configured_fallback(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
                fallback_agents=("grok",),
            ),
            "grok": AgentConfig(
                id="grok",
                command="grok ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        }
        config = _make_config(agents)
        result = select_fallback_agent(config, "claude", ["code"])
        self.assertIsNotNone(result)
        self.assertEqual(result.id, "grok")

    def test_skips_self_reference(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
                fallback_agents=("claude", "grok"),
            ),
            "grok": AgentConfig(
                id="grok",
                command="grok ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        }
        config = _make_config(agents)
        result = select_fallback_agent(config, "claude", ["code"])
        self.assertEqual(result.id, "grok")

    def test_skips_reviewer_role(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
                fallback_agents=("reviewer", "grok"),
            ),
            "reviewer": AgentConfig(
                id="reviewer",
                command="reviewer ...",
                capabilities=["code"],
                max_concurrency=1,
                role="spec_reviewer",
            ),
            "grok": AgentConfig(
                id="grok",
                command="grok ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        }
        config = _make_config(agents)
        result = select_fallback_agent(config, "claude", ["code"])
        self.assertEqual(result.id, "grok")

    def test_respects_capability_filter(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code", "tests"],
                max_concurrency=1,
                role="worker",
                fallback_agents=("grok",),
            ),
            "grok": AgentConfig(
                id="grok",
                command="grok ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        }
        config = _make_config(agents)
        # grok lacks "tests" capability
        result = select_fallback_agent(config, "claude", ["code", "tests"])
        self.assertIsNone(result)

    def test_skips_unavailable_ids(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
                fallback_agents=("grok",),
            ),
            "grok": AgentConfig(
                id="grok",
                command="grok ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        }
        config = _make_config(agents)
        result = select_fallback_agent(
            config, "claude", ["code"], unavailable_ids={"grok"}
        )
        self.assertIsNone(result)

    def test_no_fallback_configured(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        }
        config = _make_config(agents)
        result = select_fallback_agent(config, "claude", ["code"])
        self.assertIsNone(result)

    def test_empty_fallback_list(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
                fallback_agents=(),
            ),
        }
        config = _make_config(agents)
        result = select_fallback_agent(config, "claude", ["code"])
        self.assertIsNone(result)

    def test_multiple_fallbacks_returns_first_eligible(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
                fallback_agents=("pi", "grok"),
            ),
            "pi": AgentConfig(
                id="pi",
                command="pi ...",
                capabilities=["research"],
                max_concurrency=1,
                role="worker",
            ),
            "grok": AgentConfig(
                id="grok",
                command="grok ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
        }
        config = _make_config(agents)
        # pi lacks "code" capability, so grok should be selected
        result = select_fallback_agent(config, "claude", ["code"])
        self.assertEqual(result.id, "grok")

    def test_no_implicit_codex_fallback(self) -> None:
        agents = {
            "claude": AgentConfig(
                id="claude",
                command="claude ...",
                capabilities=["code"],
                max_concurrency=1,
                role="worker",
            ),
            "codex": AgentConfig(
                id="codex",
                command="codex ...",
                capabilities=["code"],
                max_concurrency=1,
                role="commander",
            ),
        }
        config = _make_config(agents)
        # No fallback_agents configured, so no implicit fallback
        result = select_fallback_agent(config, "claude", ["code"])
        self.assertIsNone(result)
