"""Global Coordinator runtime paths.

Resolves XDG-compliant directories for config, data, and state.
COORDINATOR_HOME overrides all three for testing and single-root operation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    """Immutable path contract for global Coordinator directories."""

    config_dir: Path
    data_dir: Path
    state_dir: Path

    @property
    def database(self) -> Path:
        return self.data_dir / "coordinator.db"

    @property
    def socket(self) -> Path:
        return self.state_dir / "coordinator.sock"

    @property
    def lock(self) -> Path:
        return self.state_dir / "supervisor.lock"

    def create(self) -> None:
        """Create all directories with mode 0o700."""
        for directory in (self.config_dir, self.data_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)


def resolve_runtime_paths() -> RuntimePaths:
    """Resolve global runtime paths from environment.

    Priority:
    1. COORDINATOR_HOME (test/operator override) -> {home}/config, {home}/data, {home}/state
    2. XDG variables -> {XDG_CONFIG_HOME}/coordinator, etc.
    3. Defaults -> ~/.config/coordinator, ~/.local/share/coordinator, ~/.local/state/coordinator
    """
    home = os.environ.get("COORDINATOR_HOME")
    if home:
        base = Path(home)
        return RuntimePaths(
            config_dir=base / "config",
            data_dir=base / "data",
            state_dir=base / "state",
        )

    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "coordinator"
    data_dir = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "coordinator"
    state_dir = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "coordinator"

    return RuntimePaths(
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
    )
