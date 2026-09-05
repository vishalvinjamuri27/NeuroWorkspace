from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fastapi import BackgroundTasks

from neuro_api import routes_recordings, routes_uploads


class CapturingCelery:
    calls: ClassVar[list[dict[str, Any]]] = []
    fail_next: ClassVar[bool] = False

    def __init__(self, *, broker: str) -> None:
        self.broker = broker

    def send_task(self, name: str, *, args: list[str], queue: str) -> None:
        if CapturingCelery.fail_next:
            CapturingCelery.fail_next = False
            raise ConnectionError("broker unreachable")
        self.calls.append({"broker": self.broker, "name": name, "args": args, "queue": queue})


def test_remote_worker_dispatches_to_consumed_queues(
    monkeypatch: pytest.MonkeyPatch, signed_in_client: Any
) -> None:
    workspace_id = signed_in_client.post("/api/workspaces", json={"name": "Dispatch Lab"}).json()[
        "id"
    ]
    CapturingCelery.calls = []
    monkeypatch.setattr(routes_uploads, "Celery", CapturingCelery)
    monkeypatch.setattr(routes_recordings, "Celery", CapturingCelery)
    runtime = SimpleNamespace(
        settings=SimpleNamespace(worker_eager=False, redis_url="redis://queue.example/0")
    )
    background = BackgroundTasks()
    session = signed_in_client.app.state.runtime.session_factory()
    try:
        from neuro_workspace.db.models import Job
        from neuro_workspace.domain import JobKind

        job = Job(
            workspace_id=workspace_id,
            kind=JobKind.INGESTION,
            subject_type="recording",
            subject_id="rec-test",
            idempotency_key="ingest:rec-test",
        )
        session.add(job)
        session.commit()

        routes_uploads._dispatch_ingestion(background, runtime, session, "ingest-job")
        routes_recordings._dispatch_processing(background, runtime, session, "process-job")
        routes_recordings._dispatch_deletion(background, runtime, session, "delete-job")

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
    finally:
        session.close()


def test_broker_outage_does_not_raise_and_leaves_job_undispatched(
    monkeypatch: pytest.MonkeyPatch, signed_in_client: Any
) -> None:
    from neuro_workspace.db.models import Job
    from neuro_workspace.domain import JobKind

    workspace_id = signed_in_client.post("/api/workspaces", json={"name": "Outage Lab"}).json()[
        "id"
    ]
    monkeypatch.setattr(routes_uploads, "Celery", CapturingCelery)
    CapturingCelery.calls = []
    CapturingCelery.fail_next = True
    runtime = SimpleNamespace(
        settings=SimpleNamespace(worker_eager=False, redis_url="redis://queue.example/0")
    )
    background = BackgroundTasks()
    session = signed_in_client.app.state.runtime.session_factory()
    try:
        job = Job(
            workspace_id=workspace_id,
            kind=JobKind.INGESTION,
            subject_type="recording",
            subject_id="rec-test-2",
            idempotency_key="ingest:rec-test-2",
        )
        session.add(job)
        session.commit()

        # Must not raise even though the broker call failed -- the job row is
        # durable and the outbox sweep is responsible for redriving it.
        routes_uploads._dispatch_ingestion(background, runtime, session, job.id)

        session.refresh(job)
        assert job.dispatched_at is None
    finally:
        session.close()
