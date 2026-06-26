"""Load coordinator config from global runtime paths."""

from __future__ import annotations

from .config import CoordinatorConfig, load_config_from_dir
from .runtime_paths import RuntimePaths


def load_config_for_paths(paths: RuntimePaths) -> CoordinatorConfig:
    return load_config_from_dir(paths.config_dir)