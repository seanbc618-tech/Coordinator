import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import init_git_repo, run
from local_cli_coordinator.config import AgentConfig, CoordinatorConfig, PolicyConfig, RepoConfig
from local_cli_coordinator.db import connect, create_task, get_task, init_db
from local_cli_coordinator.engine import run_one_ready_task
from local_cli_coordinator.gitops import commit_all, create_worktree, merge_branch_to_default, push_branch


def base_policy() -> PolicyConfig:
    return PolicyConfig(
        require_single_repo=True,
        require_acceptance_criteria=True,
        require_verification_commands=True,
        require_handoff_summary=True,
        max_files_touched=3,
        max_expected_minutes=30,
        max_attempts=3,
        split_if_touches_multiple_subsystems=True,
        split_if_research_and_code_are_mixed=True,
    )


def remote_branches(remote_path: Path) -> list[str]:
    result = run(
        "git",
        f"--git-dir={remote_path}",
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads",
        cwd=remote_path.parent,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return sorted(line for line in result.stdout.splitlines() if line)


def remote_file(remote_path: Path, ref: str, file_path: str) -> str:
    result = run("git", f"--git-dir={remote_path}", "show", f"{ref}:{file_path}", cwd=remote_path.parent)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


def init_bare_remote(root: Path, repo: Path) -> Path:
    remote = root / "remote.git"
    init = run("git", "init", "--bare", remote, cwd=root)
    if init.returncode != 0:
        raise AssertionError(init.stderr)
    add = run("git", "remote", "add", "origin", remote, cwd=repo)
    if add.returncode != 0:
        raise AssertionError(add.stderr)
    push = run("git", "push", "origin", "main", cwd=repo)
    if push.returncode != 0:
        raise AssertionError(push.stderr)
    return remote


def push_config(repo_path: Path) -> CoordinatorConfig:
    return CoordinatorConfig(
        agents={
            "fake": AgentConfig(
                id="fake",
                command=f"{sys.executable} -c \"from pathlib import Path; Path('feature.txt').write_text('done')\"",
                capabilities=["code"],
                max_concurrency=1,
            )
        },
        repos={
            "demo": RepoConfig(
                id="demo",
                path=repo_path,
                default_branch="main",
                remote="origin",
                branch_prefix="coord/",
                allow_push=True,
                merge_policy="push_branch_only",
                verify_commands=[
                    f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'done'\""
                ],
                review_policy="tests_only",
            )
        },
        policy=base_policy(),
    )


def latest_event_note(conn, task_id: str) -> str:
    row = conn.execute(
        "select note from events where task_id = ? order by id desc limit 1",
        (task_id,),
    ).fetchone()
    return row["note"]


class PushMergeTests(unittest.TestCase):
    def test_push_branch_pushes_worktree_head_to_remote_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            remote = init_bare_remote(root, repo)
            worktree = create_worktree(
                repo_path=repo,
                worktrees_root=root / "worktrees",
                task_id="task-push-demo",
                branch_name="coord/task-push-demo",
            )
            (worktree / "feature.txt").write_text("done\n")
            commit_all(worktree, "add feature")

            push_branch(worktree, "origin", "coord/task-push-demo")

            self.assertIn("coord/task-push-demo", remote_branches(remote))

    def test_merge_branch_to_default_fast_forwards_and_pushes_default_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            remote = init_bare_remote(root, repo)
            worktree = create_worktree(
                repo_path=repo,
                worktrees_root=root / "worktrees",
                task_id="task-merge-demo",
                branch_name="coord/task-merge-demo",
            )
            (worktree / "feature.txt").write_text("merged\n")
            commit_all(worktree, "add merged feature")

            merge_branch_to_default(repo, "coord/task-merge-demo", "main", "origin")

            self.assertEqual(remote_file(remote, "main", "feature.txt"), "merged\n")

    def test_engine_pushes_branch_when_policy_allows_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            remote = init_bare_remote(root, repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Push feature file",
                repo="demo",
                source_path="tasks/inbox/push.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )

            processed = run_one_ready_task(conn, push_config(repo), root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "done")
            self.assertEqual(latest_event_note(conn, task_id), "completed")
            self.assertIn(task["branch"], remote_branches(remote))

    def test_engine_does_not_merge_when_push_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            remote = init_bare_remote(root, repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Local only feature file",
                repo="demo",
                source_path="tasks/inbox/local.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )
            config = push_config(repo)
            config = CoordinatorConfig(
                agents=config.agents,
                repos={
                    "demo": RepoConfig(
                        id="demo",
                        path=repo,
                        default_branch="main",
                        remote="origin",
                        branch_prefix="coord/",
                        allow_push=False,
                        merge_policy="auto_merge_default_branch",
                        verify_commands=config.repos["demo"].verify_commands,
                        review_policy="tests_only",
                    )
                },
                policy=config.policy,
            )

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "done")
            self.assertNotIn(task["branch"], remote_branches(remote))
            remote_show = run(
                "git",
                f"--git-dir={remote}",
                "show",
                "main:feature.txt",
                cwd=remote.parent,
            )
            self.assertNotEqual(remote_show.returncode, 0)

    def test_engine_marks_task_failed_when_push_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Push without remote",
                repo="demo",
                source_path="tasks/inbox/push-fail.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains done"],
                verification_commands=[],
            )

            processed = run_one_ready_task(conn, push_config(repo), root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "failed")
            self.assertIn("push failed", latest_event_note(conn, task_id))

    def test_engine_marks_task_failed_when_merge_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init_git_repo(repo)
            init_bare_remote(root, repo)
            agent_script = root / "agent.py"
            agent_script.write_text(
                "from pathlib import Path\n"
                "import subprocess\n"
                f"repo = Path({str(repo)!r})\n"
                "Path('feature.txt').write_text('task\\n')\n"
                "(repo / 'README.md').write_text('diverged\\n')\n"
                "subprocess.run(['git', 'add', 'README.md'], cwd=repo, check=True)\n"
                "subprocess.run(['git', 'commit', '-m', 'diverge main'], cwd=repo, check=True)\n"
                "subprocess.run(['git', 'push', 'origin', 'main'], cwd=repo, check=True)\n"
            )
            conn = connect(root / "coordinator.db")
            init_db(conn)
            task_id = create_task(
                conn,
                title="Merge divergent branch",
                repo="demo",
                source_path="tasks/inbox/merge-fail.md",
                priority="normal",
                capabilities=["code"],
                goal="Create feature.txt.",
                acceptance_criteria=["feature.txt contains task"],
                verification_commands=[],
            )
            config = push_config(repo)
            config = CoordinatorConfig(
                agents={
                    "fake": AgentConfig(
                        id="fake",
                        command=f"{sys.executable} {agent_script}",
                        capabilities=["code"],
                        max_concurrency=1,
                    )
                },
                repos={
                    "demo": RepoConfig(
                        id="demo",
                        path=repo,
                        default_branch="main",
                        remote="origin",
                        branch_prefix="coord/",
                        allow_push=True,
                        merge_policy="auto_merge_default_branch",
                        verify_commands=[
                            f"{sys.executable} -c \"from pathlib import Path; assert Path('feature.txt').read_text() == 'task\\\\n'\""
                        ],
                        review_policy="tests_only",
                    )
                },
                policy=config.policy,
            )

            processed = run_one_ready_task(conn, config, root)

            self.assertTrue(processed)
            task = get_task(conn, task_id)
            self.assertEqual(task["state"], "failed")
            self.assertIn("merge failed", latest_event_note(conn, task_id))
