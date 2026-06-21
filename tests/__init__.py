"""Test package — treat leaked sockets and DB handles as hard failures."""

from __future__ import annotations

import gc
import sqlite3
import sys
import threading
import unittest
import warnings

warnings.simplefilter("error", ResourceWarning)

_current_test_connections: list["_TrackedConnection"] = []


class _TrackedConnection:
    """Proxy that records coordinator DB connections for leak audits."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_thread_id", threading.get_ident())
        _current_test_connections.append(self)

    def close(self) -> None:
        object.__setattr__(self, "_closed", True)
        self._conn.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def __setattr__(self, name: str, value) -> None:
        if name in {"_conn", "_closed", "_thread_id"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


def _audit_open_connections() -> None:
    leaked = [conn for conn in _current_test_connections if not conn._closed]
    if leaked:
        main_thread = threading.main_thread().ident
        background = [c for c in leaked if c._thread_id != main_thread]
        suffix = ""
        if background:
            suffix = f" (including {len(background)} background-thread leak(s))"
        raise ResourceWarning(
            f"{len(leaked)} unclosed coordinator DB connection(s): "
            f"{leaked[0]._conn!r}{suffix}"
        )


def _reset_connection_tracking() -> None:
    main_thread = threading.main_thread().ident
    for conn in _current_test_connections:
        if not conn._closed and conn._thread_id == main_thread:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    _current_test_connections.clear()


def _install_tracked_connect() -> None:
    import local_cli_coordinator.db as db_module

    original = db_module.connect

    def tracked_connect(path):
        return _TrackedConnection(original(path))

    db_module.connect = tracked_connect  # type: ignore[assignment]


_install_tracked_connect()

_original_testcase_run = unittest.TestCase.run


def _run_with_resource_guard(self, result=None):
    _reset_connection_tracking()
    leak_info: tuple[type[BaseException], BaseException, object] | None = None
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        try:
            outcome = _original_testcase_run(self, result)
        finally:
            gc.collect()
            try:
                _audit_open_connections()
            except ResourceWarning:
                leak_info = sys.exc_info()
            _reset_connection_tracking()
    if leak_info is not None and result is not None:
        result.addFailure(self, leak_info)
        return False
    return outcome


unittest.TestCase.run = _run_with_resource_guard