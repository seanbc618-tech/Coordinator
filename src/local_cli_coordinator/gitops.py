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
    repo_path = repo_path.resolve()
    worktree_path = (worktrees_root / task_id).resolve()
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


def merge_base(worktree_path: Path, ref: str) -> str:
    result = git(["merge-base", "HEAD", ref], cwd=worktree_path)
    require_success(result, f"find merge base with {ref}")
    return result.stdout.strip()


def collect_changed_files_since(worktree_path: Path, base_ref: str) -> list[str]:
    result = git(["diff", "--name-only", "-z", base_ref, "--", "."], cwd=worktree_path)
    require_success(result, "collect branch changed files")
    files = {path for path in result.stdout.split("\0") if path}
    untracked = git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=worktree_path)
    require_success(untracked, "list untracked files")
    files.update(path for path in untracked.stdout.split("\0") if path)
    return sorted(files)


def diff_patch(worktree_path: Path, base_ref: str = "HEAD") -> str:
    untracked_result = git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=worktree_path)
    require_success(untracked_result, "list untracked files")
    untracked_files = [path for path in untracked_result.stdout.split("\0") if path]

    if untracked_files:
        intent = git(["add", "--intent-to-add", "--", *untracked_files], cwd=worktree_path)
        require_success(intent, "mark untracked files for diff")

    try:
        result = git(["diff", base_ref, "--", "."], cwd=worktree_path)
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


def push_branch(worktree_path: Path, remote: str, branch_name: str) -> None:
    result = git(["push", remote, f"HEAD:{branch_name}"], cwd=worktree_path)
    require_success(result, "push branch")


def merge_branch_to_default(repo_path: Path, branch_name: str, default_branch: str, remote: str) -> None:
    checkout = git(["checkout", default_branch], cwd=repo_path)
    require_success(checkout, "checkout default branch")
    pull = git(["pull", "--ff-only", remote, default_branch], cwd=repo_path)
    require_success(pull, "pull default branch")
    merge = git(["merge", "--ff-only", branch_name], cwd=repo_path)
    require_success(merge, "merge branch")
    push = git(["push", remote, default_branch], cwd=repo_path)
    require_success(push, "push default branch")


def list_worktrees(repo_path: Path) -> list[dict[str, str]]:
    """List git worktrees with their path, HEAD, and branch info."""
    result = git(["worktree", "list", "--porcelain"], cwd=repo_path)
    require_success(result, "list worktrees")
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        if " " in line:
            key, value = line.split(" ", 1)
            current[key] = value
        else:
            current[line] = ""
    if current:
        worktrees.append(current)
    return worktrees


def worktree_has_uncommitted_changes(worktree_path: Path) -> bool:
    """Check whether a worktree has uncommitted changes."""
    result = git(["status", "--porcelain", "-z"], cwd=worktree_path)
    return result.returncode == 0 and result.stdout.strip() != ""


def remove_worktree(repo_path: Path, worktree_path: Path, *, force: bool = False) -> None:
    """Remove a git worktree."""
    cmd = ["worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(worktree_path))
    result = git(cmd, cwd=repo_path)
    require_success(result, f"remove worktree {worktree_path}")


def stale_worktrees(repo_path: Path, worktrees_root: Path) -> list[dict[str, str]]:
    """Identify worktrees whose task directory no longer exists or are stuck."""
    all_worktrees = list_worktrees(repo_path)
    stale: list[dict[str, str]] = []
    for wt in all_worktrees:
        wt_path = Path(wt.get("worktree", ""))
        if not wt_path.exists():
            stale.append(wt)
        elif worktrees_root in wt_path.parents or wt_path == worktrees_root:
            # This is a coordinator-managed worktree
            stale.append(wt)
    return stale
