"""Local-file project shape inspection for onboarding."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .init_project import derive_repo_id

PYTHON_SIGNALS = (
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "uv.lock",
)
NODE_SIGNALS = (
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)
NODE_CONFIG_GLOBS = ("vite.config.ts", "vite.config.js", "vite.config.mjs")
SKIP_WALK_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "dist",
    "build",
    "__pycache__",
}


@dataclass(frozen=True)
class ProjectInspection:
    repo_root: Path
    detected_profile: str
    recommended_preset: str
    confidence: float
    findings: list[dict[str, str]] = field(default_factory=list)
    verify_commands: list[str] = field(default_factory=list)
    repo_id: str = ""


def inspect_project_shape(
    path: Path,
    *,
    allow_non_git: bool = False,
) -> ProjectInspection:
    """Inspect repository shape from local files only; never run verify commands."""
    start = Path(path)
    if not start.exists():
        raise ValueError(f"path does not exist: {start}")

    if allow_non_git:
        repo_root = start.resolve()
    else:
        try:
            repo_root = _find_git_root(start)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    signals = _collect_signals(repo_root)
    profile, confidence, findings = _classify_profile(repo_root, signals)
    verify_commands = _suggest_verify_commands(repo_root, profile, signals)
    return ProjectInspection(
        repo_root=repo_root,
        detected_profile=profile,
        recommended_preset="observe",
        confidence=confidence,
        findings=findings,
        verify_commands=verify_commands,
        repo_id=derive_repo_id(repo_root),
    )


def _find_git_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ValueError(f"not a git repository: {start}")


def format_inspection_summary(
    inspection: ProjectInspection,
    *,
    home: Path | None = None,
) -> str:
    """Human-readable inspection summary with sensitive paths redacted."""
    payload = {
        "profile": inspection.detected_profile,
        "preset": inspection.recommended_preset,
        "confidence": inspection.confidence,
        "repo_id": inspection.repo_id,
        "verify_commands": inspection.verify_commands,
        "findings": inspection.findings,
    }
    text = json.dumps(payload, indent=2)
    return _redact_sensitive_paths(text, home=home, repo_root=inspection.repo_root)


def _redact_sensitive_paths(
    text: str,
    *,
    home: Path | None,
    repo_root: Path,
) -> str:
    redacted = text
    for candidate in {str(repo_root), str(repo_root.resolve())}:
        redacted = redacted.replace(candidate, "<repo>")
    if home is not None:
        home_text = str(home)
        redacted = redacted.replace(home_text, "<home>")
        redacted = re.sub(r"/Users/[^\"/\s]+", "<home>", redacted)
    redacted = re.sub(r"/Users/[^\"/\s]+", "<user>", redacted)
    return redacted


def _collect_signals(repo_root: Path) -> dict[str, bool]:
    signals = {name: (repo_root / name).is_file() for name in PYTHON_SIGNALS}
    signals.update({name: (repo_root / name).is_file() for name in NODE_SIGNALS})
    signals["vite_config"] = any(
        (repo_root / name).is_file() for name in NODE_CONFIG_GLOBS
    )
    signals["has_tests_dir"] = (repo_root / "tests").is_dir()
    signals["has_pytest_ini"] = (repo_root / "pytest.ini").is_file()
    signals["pyproject_declares_pytest"] = _pyproject_declares_pytest(repo_root)
    signals["package_json"] = (repo_root / "package.json").is_file()
    return signals


def _pyproject_declares_pytest(repo_root: Path) -> bool:
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return "pytest" in text.lower()


def _package_scripts(repo_root: Path) -> dict[str, str]:
    package_json = repo_root / "package.json"
    if not package_json.is_file():
        return {}
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(key): str(value) for key, value in scripts.items()}


def _markdown_file_count(repo_root: Path) -> int:
    count = 0
    for path in repo_root.iterdir():
        if path.is_file() and path.suffix.lower() in {".md", ".markdown"}:
            count += 1
    docs_dir = repo_root / "docs"
    if docs_dir.is_dir():
        for path in docs_dir.rglob("*.md"):
            if any(part in SKIP_WALK_DIRS for part in path.parts):
                continue
            count += 1
    return count


def _has_app_package_files(repo_root: Path) -> bool:
    for name in (*PYTHON_SIGNALS, *NODE_SIGNALS):
        if (repo_root / name).is_file():
            return True
    return any((repo_root / name).is_file() for name in NODE_CONFIG_GLOBS)


def _classify_profile(
    repo_root: Path,
    signals: dict[str, bool],
) -> tuple[str, float, list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    python = any(signals.get(name) for name in PYTHON_SIGNALS)
    node = signals.get("package_json") or signals.get("vite_config") or any(
        signals.get(name) for name in ("pnpm-lock.yaml", "yarn.lock")
    )
    if python:
        findings.append({"signal": "python", "detail": "python packaging files detected"})
    if node:
        findings.append({"signal": "node", "detail": "node tooling files detected"})

    if python and node:
        return "mixed", 0.9, findings
    if python:
        return "python", 0.85, findings
    if node:
        return "node", 0.85, findings

    md_count = _markdown_file_count(repo_root)
    if md_count >= 2 and not _has_app_package_files(repo_root):
        findings.append({"signal": "docs", "detail": f"{md_count} markdown files"})
        return "docs", 0.7, findings

    if md_count == 1 and not _has_app_package_files(repo_root):
        findings.append({"signal": "sparse", "detail": "single markdown file only"})
    return "unknown", 0.3, findings


def _suggest_verify_commands(
    repo_root: Path,
    profile: str,
    signals: dict[str, bool],
) -> list[str]:
    commands: list[str] = []
    if profile in {"python", "mixed"}:
        if signals.get("has_tests_dir"):
            commands.append("python3 -m unittest discover -s tests -q")
        if signals.get("has_pytest_ini") or signals.get("pyproject_declares_pytest"):
            commands.append("uv run pytest -q")
    if profile in {"node", "mixed"}:
        scripts = _package_scripts(repo_root)
        if "test" in scripts:
            commands.append("npm test -- --run")
        if "lint" in scripts:
            commands.append("npm run lint")
        if "typecheck" in scripts:
            commands.append("npm run typecheck")
    if profile == "mixed":
        return commands[:4]
    return commands