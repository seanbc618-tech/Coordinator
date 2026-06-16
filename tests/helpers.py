import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [sys.executable, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run("git", "init", "-b", "main", cwd=path)
    run("git", "config", "user.email", "coordinator@example.local", cwd=path)
    run("git", "config", "user.name", "Coordinator Test", cwd=path)
    (path / "README.md").write_text("demo\n")
    run("git", "add", "README.md", cwd=path)
    result = run("git", "commit", "-m", "initial", cwd=path)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
