import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from local_cli_coordinator.discovery import load_findings, write_findings
from local_cli_coordinator.models import Finding


class FindingModelTests(unittest.TestCase):
    def test_finding_has_required_fields(self) -> None:
        names = {field.name for field in fields(Finding)}
        self.assertEqual(
            names,
            {
                "id",
                "repo",
                "source",
                "title",
                "body",
                "severity",
                "evidence",
                "discovered_at",
            },
        )

    def test_write_creates_findings_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_findings(root, "git-recent.jsonl", [])

            findings_dir = root / "state" / "findings"
            self.assertTrue(findings_dir.is_dir())
            self.assertTrue((findings_dir / "git-recent.jsonl").is_file())

    def test_round_trip_preserves_fields_and_order(self) -> None:
        findings = [
            Finding(
                id="finding-abc123",
                repo="polymarket-weather-arb",
                source="git_recent_commits",
                title="Recent commit touches pricing rules",
                body="Commit 88d8ede updates low-temperature title handling.",
                severity="info",
                evidence={"commit": "88d8ede", "subject": "fix pricing titles"},
                discovered_at="2026-06-19T10:15:30Z",
            ),
            Finding(
                id="finding-def456",
                repo="coordinator",
                source="command",
                title="CI reported failing tests",
                body="test_verify.py::test_timeout failed on main.",
                severity="high",
                evidence={"command": "uv run pytest -q", "exit_code": "1"},
                discovered_at="2026-06-19T11:00:00Z",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_findings(root, "batch.jsonl", findings)
            loaded = load_findings(root, "batch.jsonl")

            self.assertEqual(path, root / "state" / "findings" / "batch.jsonl")
            self.assertEqual(loaded, findings)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            for line, finding in zip(lines, findings, strict=True):
                self.assertEqual(json.loads(line), json.loads(json.dumps(finding.__dict__)))

    def test_load_missing_file_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = load_findings(Path(tmp), "missing.jsonl")
            self.assertEqual(loaded, [])