"""Worker clients (DB engine, object store, S3 signal store) must be built in
the process that uses them: fsspec's S3 store is not fork-safe, so building
them at import time in Celery's parent process is exactly the bug the
worker_process_init hook exists to avoid.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

import neuro_worker.celery_app as worker


def test_importing_the_worker_module_builds_no_clients() -> None:
    probe = (
        "import neuro_worker.celery_app as w; "
        "assert w._initialized_pid is None and w.factory is None and w.signal_store is None"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_clients_are_built_once_per_process_and_rebuilt_after_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds: list[int] = []

    def fake_engine_and_factory(*_args: object, **_kwargs: object) -> tuple[str, str]:
        builds.append(os.getpid())
        return "engine", "factory"

    monkeypatch.setattr(worker, "create_engine_and_factory", fake_engine_and_factory)
    monkeypatch.setattr(worker, "create_object_store", lambda _settings: "object-store")
    monkeypatch.setattr(worker, "create_signal_store", lambda _settings: "signal-store")
    for name in ("engine", "factory", "object_store", "signal_store", "_initialized_pid"):
        monkeypatch.setattr(worker, name, None)

    worker._ensure_worker_resources()
    worker._ensure_worker_resources()
    assert len(builds) == 1
    assert worker.factory == "factory"

    parent_pid = os.getpid()
    monkeypatch.setattr(worker.os, "getpid", lambda: parent_pid + 1)  # a forked child
    worker._ensure_worker_resources()
    assert len(builds) == 2
