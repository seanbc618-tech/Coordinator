import tempfile
import unittest
from pathlib import Path

from local_cli_coordinator.discovery import (
    delete_finding,
    findings_dir,
    list_findings,
    load_finding,
    save_finding,
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
