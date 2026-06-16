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
    result = git(["status", "--porcelain=v1", "-z"], cwd=worktree_path)
    require_success(result, "collect changed files")
    files: list[str] = []
    records = result.stdout.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        if not record:
            index += 1
            continue
        status = record[:2]
        path = record[3:]
        if "R" in status or "C" in status:
            index += 1
            if index < len(records) and records[index]:
                files.append(records[index])
            files.append(path)
        else:
            files.append(path)
        index += 1
    return sorted(files)


def diff_patch(worktree_path: Path) -> str:
    untracked_result = git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=worktree_path)
    require_success(untracked_result, "list untracked files")
    untracked_files = [path for path in untracked_result.stdout.split("\0") if path]

    if untracked_files:
        intent = git(["add", "--intent-to-add", "--", *untracked_files], cwd=worktree_path)
        require_success(intent, "mark untracked files for diff")

    try:
        result = git(["diff", "HEAD", "--", "."], cwd=worktree_path)
        require_success(result, "collect diff")
        return result.stdout
    finally:
        if untracked_files:
            reset = git(["reset", "-q", "--", *untracked_files], cwd=worktree_path)
            require_success(reset, "clear untracked intent-to-add markers")


def commit_all(worktree_path: Path, message: str) -> str:
    add_result = git(["add", "--all"], cwd=worktree_path)
    require_success(add_result, "git add")
    commit_result = git(["commit", "-m", message], cwd=worktree_path)
    require_success(commit_result, "git commit")
    rev_result = git(["rev-parse", "HEAD"], cwd=worktree_path)
    require_success(rev_result, "read commit hash")
    return rev_result.stdout.strip()
