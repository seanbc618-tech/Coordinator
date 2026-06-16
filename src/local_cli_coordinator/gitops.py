from pathlib import Path
import subprocess


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def require_success(result: subprocess.CompletedProcess[str], action: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(f"{action} failed: {result.stderr.strip()}")


def is_git_repo(path: Path) -> bool:
    result = git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return result.returncode == 0 and result.stdout.strip() == "true"


def create_worktree(
    *,
    repo_path: Path,
    worktrees_root: Path,
    task_id: str,
    branch_name: str,
) -> Path:
    worktree_path = worktrees_root / task_id
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = git(["worktree", "add", "-b", branch_name, str(worktree_path)], cwd=repo_path)
    require_success(result, "create worktree")
    return worktree_path


def collect_changed_files(worktree_path: Path) -> list[str]:
    result = git(["status", "--porcelain"], cwd=worktree_path)
    require_success(result, "collect changed files")
    files: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        files.append(line[3:])
    return sorted(files)


def diff_patch(worktree_path: Path) -> str:
    intent = git(["add", "--intent-to-add", "--all"], cwd=worktree_path)
    require_success(intent, "mark untracked files for diff")
    result = git(["diff", "--", "."], cwd=worktree_path)
    require_success(result, "collect diff")
    return result.stdout
