import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from local_cli_coordinator.discovery import (
    delete_finding,
    findings_dir,
    list_findings,
    load_finding,
    load_findings,
    save_finding,
    write_findings,
)
from local_cli_coordinator.models import Finding


def _sample_finding(**overrides) -> Finding:
    defaults = dict(
        id="find-001",
        repo="demo",
        source="git_recent_commits",
        title="Fix authentication bug",
        body="Commit abc123 fixes a login issue.",
        severity="info",
        evidence="commit abc123",
    )
    defaults.update(overrides)
    return Finding(**defaults)


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

    def test_to_dict_roundtrip(self) -> None:
        original = _sample_finding()
        restored = Finding.from_dict(original.to_dict())
        self.assertEqual(original.id, restored.id)
        self.assertEqual(original.repo, restored.repo)
        self.assertEqual(original.source, restored.source)
        self.assertEqual(original.title, restored.title)
        self.assertEqual(original.body, restored.body)
        self.assertEqual(original.severity, restored.severity)
        self.assertEqual(original.evidence, restored.evidence)
        self.assertEqual(original.discovered_at, restored.discovered_at)

    def test_from_dict_defaults_missing_optional_fields(self) -> None:
        data = {"id": "x", "repo": "r", "source": "s", "title": "t"}
        finding = Finding.from_dict(data)
        self.assertEqual(finding.body, "")
        self.assertEqual(finding.severity, "info")
        self.assertEqual(finding.evidence, "")

    def test_discovered_at_auto_populated(self) -> None:
        finding = _sample_finding()
        self.assertIsInstance(finding.discovered_at, str)
        self.assertTrue(len(finding.discovered_at) > 0)

    def test_write_creates_findings_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_findings(root, "git-recent.jsonl", [])

            directory = findings_dir(root)
            self.assertTrue(directory.is_dir())
            self.assertTrue((directory / "git-recent.jsonl").is_file())

    def test_round_trip_preserves_fields_and_order(self) -> None:
        findings = [
            Finding(
                id="finding-abc123",
                repo="polymarket-weather-arb",
                source="git_recent_commits",
                title="Recent commit touches pricing rules",
                body="Commit 88d8ede updates low-temperature title handling.",
                severity="info",
                evidence="commit=88d8ede;subject=fix pricing titles",
                discovered_at="2026-06-19T10:15:30Z",
            ),
            Finding(
                id="finding-def456",
                repo="coordinator",
                source="command",
                title="CI reported failing tests",
                body="test_verify.py::test_timeout failed on main.",
                severity="high",
                evidence="command=uv run pytest -q;exit_code=1",
                discovered_at="2026-06-19T11:00:00Z",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_findings(root, "batch.jsonl", findings)
            loaded = load_findings(root, "batch.jsonl")

            self.assertEqual(path, findings_dir(root) / "batch.jsonl")
            self.assertEqual(loaded, findings)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            for line, finding in zip(lines, findings, strict=True):
                self.assertEqual(json.loads(line), finding.to_dict())

    def test_load_missing_file_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = load_findings(Path(tmp), "missing.jsonl")
            self.assertEqual(loaded, [])


class FindingPersistenceTests(unittest.TestCase):
    def test_save_and_load_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = _sample_finding()
            save_finding(root, original)
            loaded = load_finding(root, "find-001")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, "find-001")
        self.assertEqual(loaded.title, "Fix authentication bug")

    def test_load_finding_returns_none_for_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(load_finding(root, "nonexistent"))

    def test_list_findings_returns_all_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_finding(root, _sample_finding(id="f1", title="First"))
            save_finding(root, _sample_finding(id="f2", title="Second"))
            save_finding(root, _sample_finding(id="f3", title="Third"))

            results = list_findings(root)

        self.assertEqual(len(results), 3)
        ids = {f.id for f in results}
        self.assertEqual(ids, {"f1", "f2", "f3"})

    def test_list_findings_returns_empty_for_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(list_findings(root), [])

    def test_delete_finding_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_finding(root, _sample_finding())
            self.assertTrue(delete_finding(root, "find-001"))
            self.assertIsNone(load_finding(root, "find-001"))

    def test_delete_finding_returns_false_for_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(delete_finding(root, "nonexistent"))

    def test_findings_dir_points_to_state_findings(self) -> None:
        root = Path("/tmp/test-root")
        self.assertEqual(findings_dir(root), root / "state" / "findings")

    def test_save_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Ensure state/findings/ does not exist yet
            self.assertFalse((root / "state" / "findings").exists())
            save_finding(root, _sample_finding())
            self.assertTrue((root / "state" / "findings").exists())

    def test_corrupted_jsonl_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_finding(root, _sample_finding(id="good"))
            bad = findings_dir(root) / "bad.jsonl"
            bad.write_text("not valid json\n", encoding="utf-8")
            results = list_findings(root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "good")
