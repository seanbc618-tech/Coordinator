import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.models import Finding, TaskDraft
from local_cli_coordinator.planner import PlanResult, plan_finding, plan_findings
from local_cli_coordinator.tasks import write_generated_task


def _finding(**overrides) -> Finding:
    defaults = dict(
        id="find-001",
        repo="demo",
        source="git_recent_commits",
        title="Fix login timeout",
        body="- Increase timeout to 30s\n- Add retry with backoff",
        severity="info",
        evidence="commit abc123",
    )
    defaults.update(overrides)
    return Finding(**defaults)


class PlanFindingTests(unittest.TestCase):
    def test_small_finding_produces_one_task(self) -> None:
        finding = _finding()
        result = plan_finding(finding)

        self.assertEqual(len(result.needs_split), 0)
        self.assertEqual(len(result.tasks), 1)

        task = result.tasks[0]
        self.assertEqual(task.title, "Fix login timeout")
        self.assertEqual(task.repo, "demo")
        self.assertIn("Increase timeout to 30s", task.acceptance_criteria)
        self.assertIn("Add retry with backoff", task.acceptance_criteria)
        self.assertEqual(task.source_path, "state/findings/find-001.jsonl")

    def test_broad_finding_is_rejected(self) -> None:
        finding = _finding(title="Refactor entire authentication system")
        result = plan_finding(finding)

        self.assertEqual(len(result.tasks), 0)
        self.assertTrue(len(result.needs_split) > 0)
        self.assertIn("broad", result.needs_split[0].lower())

    def test_vague_finding_is_rejected(self) -> None:
        finding = _finding(
            title="Maybe investigate the login issue somehow"
        )
        result = plan_finding(finding)

        self.assertEqual(len(result.tasks), 0)
        self.assertTrue(len(result.needs_split) > 0)
        self.assertIn("vague", result.needs_split[0].lower())

    def test_too_many_criteria_is_rejected(self) -> None:
        criteria = "\n".join(f"- Criterion {i}" for i in range(10))
        finding = _finding(body=criteria)
        result = plan_finding(finding)

        self.assertEqual(len(result.tasks), 0)
        self.assertTrue(len(result.needs_split) > 0)
        self.assertIn("criteria", result.needs_split[0].lower())

    def test_empty_body_is_rejected(self) -> None:
        finding = _finding(body="")
        result = plan_finding(finding)

        self.assertEqual(len(result.tasks), 0)
        self.assertTrue(len(result.needs_split) > 0)
        self.assertIn("no extractable", result.needs_split[0].lower())

    def test_body_without_bullets_uses_body_as_single_criterion(self) -> None:
        finding = _finding(body="Increase the login timeout to 30 seconds.")
        result = plan_finding(finding)

        self.assertEqual(len(result.needs_split), 0)
        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(
            result.tasks[0].acceptance_criteria,
            ["Increase the login timeout to 30 seconds."],
        )

    def test_capability_derived_from_source(self) -> None:
        for source, expected_caps in [
            ("git_recent_commits", ["code"]),
            ("ci_command", ["code", "test"]),
            ("issue_command", ["code"]),
            ("command", ["code"]),
        ]:
            with self.subTest(source=source):
                finding = _finding(source=source, body="- Do one thing")
                result = plan_finding(finding)
                self.assertEqual(result.tasks[0].capabilities, expected_caps)


class PlanFindingsBatchTests(unittest.TestCase):
    def test_batch_plans_multiple_findings(self) -> None:
        findings = [
            _finding(id="f1", title="Fix A", body="- Do A"),
            _finding(id="f2", title="Fix B", body="- Do B"),
        ]
        result = plan_findings(findings)

        self.assertEqual(len(result.tasks), 2)
        self.assertEqual(len(result.needs_split), 0)

    def test_batch_collects_split_reasons(self) -> None:
        findings = [
            _finding(id="f1", title="Fix A", body="- Do A"),
            _finding(id="f2", title="Refactor everything", body=""),
        ]
        result = plan_findings(findings)

        self.assertEqual(len(result.tasks), 1)
        self.assertTrue(len(result.needs_split) > 0)


class PlanResultIntegrationTests(unittest.TestCase):
    def test_planned_task_includes_source_path_in_generated_file(self) -> None:
        finding = _finding()
        result = plan_finding(finding)
        task = result.tasks[0]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_generated_task(root, task)
            content = path.read_text()

        self.assertIn("source: state/findings/find-001.jsonl", content)
