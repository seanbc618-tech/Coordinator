"""Tests for the project registry."""

import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase

from local_cli_coordinator.db import connect, init_db
from local_cli_coordinator.projects import (
    ProjectDraft,
    inspect_project,
    register_project,
    find_project_by_path,
    list_projects,
)


class InspectProjectTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, capture_output=True)
        (self.repo / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            },
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_inspect_returns_draft(self) -> None:
        draft = inspect_project(self.repo)
        self.assertIsInstance(draft, ProjectDraft)
        self.assertEqual(draft.canonical_path, self.repo.resolve())
        self.assertIsNotNone(draft.repo_id)

    def test_inspect_subdirectory(self) -> None:
        subdir = self.repo / "src"
        subdir.mkdir()
        draft = inspect_project(subdir)
        self.assertEqual(draft.canonical_path, self.repo.resolve())

    def test_rejects_non_git_directory(self) -> None:
        not_git = Path(self.tmp.name) / "not_git"
        not_git.mkdir()
        with self.assertRaises(ValueError):
            inspect_project(not_git)


class RegisterProjectTest(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        init_db(self.conn)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, capture_output=True)
        (self.repo / "README.md").write_text("test")
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        self.draft = inspect_project(self.repo)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_register_requires_confirmation(self) -> None:
        with self.assertRaises(PermissionError):
            register_project(self.conn, self.draft, confirmed=False)

    def test_register_confirmed(self) -> None:
        project_id = register_project(self.conn, self.draft, confirmed=True)
        self.assertIsNotNone(project_id)
        projects = list_projects(self.conn)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["id"], project_id)

    def test_idempotent_registration(self) -> None:
        id1 = register_project(self.conn, self.draft, confirmed=True)
        id2 = register_project(self.conn, self.draft, confirmed=True)
        self.assertEqual(id1, id2)
        self.assertEqual(len(list_projects(self.conn)), 1)

    def test_find_by_path(self) -> None:
        register_project(self.conn, self.draft, confirmed=True)
        found = find_project_by_path(self.conn, self.repo)
        self.assertIsNotNone(found)
        self.assertEqual(found["canonical_path"], str(self.repo.resolve()))

    def test_find_by_subdirectory(self) -> None:
        register_project(self.conn, self.draft, confirmed=True)
        subdir = self.repo / "src"
        subdir.mkdir()
        found = find_project_by_path(self.conn, subdir)
        self.assertIsNotNone(found)

    def test_symlink_path(self) -> None:
        link = Path(self.tmp.name) / "link"
        link.symlink_to(self.repo)
        register_project(self.conn, self.draft, confirmed=True)
        found = find_project_by_path(self.conn, link)
        self.assertIsNotNone(found)
