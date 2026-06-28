"""Load coordinator config from global runtime paths."""

from __future__ import annotations

from .config import CoordinatorConfig, load_config_from_dir
from .runtime_paths import RuntimePaths

REQUIRED_CONFIG_FILES = ("agents.toml", "repos.toml", "policy.toml")


def ensure_config_dir(paths: RuntimePaths) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)


def config_files_present(paths: RuntimePaths) -> bool:
    return all((paths.config_dir / name).is_file() for name in REQUIRED_CONFIG_FILES)


def load_config_for_paths(paths: RuntimePaths) -> CoordinatorConfig:
    return load_config_from_dir(paths.config_dir)