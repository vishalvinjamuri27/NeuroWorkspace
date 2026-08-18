from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fastapi import BackgroundTasks

from neuro_api import routes_recordings, routes_uploads


class CapturingCelery:
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *, broker: str) -> None:
        self.broker = broker

    def send_task(self, name: str, *, args: list[str], queue: str) -> None:
        self.calls.append({"broker": self.broker, "name": name, "args": args, "queue": queue})


def test_remote_worker_dispatches_to_consumed_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    CapturingCelery.calls = []
    monkeypatch.setattr(routes_uploads, "Celery", CapturingCelery)
    monkeypatch.setattr(routes_recordings, "Celery", CapturingCelery)
    runtime = SimpleNamespace(
        settings=SimpleNamespace(worker_eager=False, redis_url="redis://queue.example/0")
    )
    background = BackgroundTasks()

    routes_uploads._dispatch_ingestion(background, runtime, "ingest-job")
    routes_recordings._dispatch_processing(background, runtime, "process-job")
    routes_recordings._dispatch_deletion(background, runtime, "delete-job")

    assert CapturingCelery.calls == [
        {
            "broker": "redis://queue.example/0",
            "name": "neuroworkspace.ingest",
            "args": ["ingest-job"],
            "queue": "ingestion",
        },
        {
            "broker": "redis://queue.example/0",
            "name": "neuroworkspace.process",
            "args": ["process-job"],
            "queue": "processing",
        },
        {
            "broker": "redis://queue.example/0",
            "name": "neuroworkspace.delete",
            "args": ["delete-job"],
            "queue": "maintenance",
        },
    ]
