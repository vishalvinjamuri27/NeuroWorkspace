"""Phase 2 distributed-processing hardening: lease heartbeat during a long
blocking call, cooperative cancellation of QUEUED and RUNNING jobs, and
structured failure metadata across all three executors.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from neuro_workspace.config import Settings
from neuro_workspace.db.models import Base, Job, Workspace
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.domain import JobKind, JobState
from neuro_workspace.services.jobs import (
    LeaseHeartbeat,
    claim_job,
    is_cancel_requested,
    request_cancellation,
)
from test_api_integration import create_workspace_project, upload_csv


@pytest.fixture
def factory(tmp_path):
    settings = Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'jobs.db'}")
    engine, factory = create_engine_and_factory(settings.database_url)
    Base.metadata.create_all(engine)
    return factory


def _make_job(factory, **overrides) -> str:
    session = factory()
    try:
        workspace = Workspace(name="Lab", slug=f"lab-{datetime.now(UTC).timestamp()}")
        session.add(workspace)
        session.flush()
        job = Job(
            workspace_id=workspace.id,
            kind=JobKind.INGESTION,
            subject_type="recording",
            subject_id="rec-1",
            idempotency_key=f"ingest:rec-1:{datetime.now(UTC).timestamp()}",
            **overrides,
        )
        session.add(job)
        session.commit()
        return job.id
    finally:
        session.close()


def test_lease_heartbeat_keeps_a_short_lease_alive_across_a_long_call(factory) -> None:
    """Without a heartbeat, a 1s lease would expire during a ~2.5s blocking
    call and a second worker could claim the job out from under the first.
    """

    job_id = _make_job(factory)
    session = factory()
    try:
        claimed, _ = claim_job(session, job_id, lease_seconds=1)
        assert claimed is True
    finally:
        session.close()

    with LeaseHeartbeat(factory, job_id, lease_seconds=1, interval_seconds=0.3):
        time.sleep(1.2)

    other_session = factory()
    try:
        # A competing claim attempt right after the heartbeat stopped must
        # still lose: the lease was kept fresh throughout, not just at start.
        claimed_by_other, _ = claim_job(other_session, job_id, lease_seconds=1)
        assert claimed_by_other is False
    finally:
        other_session.close()


def test_cancelling_a_queued_job_is_immediate(factory) -> None:
    job_id = _make_job(factory)
    session = factory()
    try:
        job = request_cancellation(session, job_id)
        assert job is not None
        assert job.status == JobState.CANCELLED

        # A worker that tries to claim it afterward must not be able to.
        claimed, _ = claim_job(session, job_id)
        assert claimed is False
    finally:
        session.close()


def test_cancelling_a_running_job_is_cooperative(factory) -> None:
    job_id = _make_job(factory)
    session = factory()
    try:
        claim_job(session, job_id)
        job = request_cancellation(session, job_id)
        assert job is not None
        assert job.status == JobState.CANCEL_REQUESTED

        # The worker is still "running" as far as claiming goes -- a second
        # worker must not pick up a job whose original owner hasn't yet
        # observed the cancellation request.
        claimed_by_other, _ = claim_job(session, job_id)
        assert claimed_by_other is False

        assert is_cancel_requested(session, job_id) is True
    finally:
        session.close()


def test_cancelling_a_terminal_job_is_a_no_op(factory) -> None:
    job_id = _make_job(factory)
    session = factory()
    try:
        claim_job(session, job_id)
        job = session.get(Job, job_id)
        job.status = JobState.SUCCEEDED
        session.commit()

        result = request_cancellation(session, job_id)
        assert result is not None
        assert result.status == JobState.SUCCEEDED  # unchanged, not an error
    finally:
        session.close()


def test_cancel_endpoint_immediately_cancels_a_queued_processing_job(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)

    factory = client.app.state.runtime.session_factory
    session = factory()
    try:
        workspace_id = client.get(f"/api/recordings/{recording_id}").json()["workspace_id"]
        job = Job(
            workspace_id=workspace_id,
            kind=JobKind.PROCESSING,
            subject_type="recording",
            subject_id=recording_id,
            idempotency_key=f"process:{recording_id}:manual-test",
            status=JobState.QUEUED,
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"


def test_cancel_endpoint_requires_membership(signed_in_client: TestClient) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)

    factory = client.app.state.runtime.session_factory
    session = factory()
    try:
        workspace_id = client.get(f"/api/recordings/{recording_id}").json()["workspace_id"]
        job = Job(
            workspace_id=workspace_id,
            kind=JobKind.PROCESSING,
            subject_type="recording",
            subject_id=recording_id,
            idempotency_key=f"process:{recording_id}:outsider-test",
            status=JobState.QUEUED,
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    from conftest import sign_in

    sign_in(client, "outsider@example.test")
    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 404


def test_processing_failure_records_structured_diagnostic_id(
    signed_in_client: TestClient,
) -> None:
    """Forces a real failure inside execute_processing_job (a ProcessingRun
    pointing at a version id that does not exist in the signal store) and
    checks the shared _error_report() path populated diagnostic_id -- this
    was previously silently absent for processing/deletion failures.
    """

    from neuro_workspace.db.models import Job, ProcessingRun, User
    from neuro_workspace.domain import JobKind, JobState
    from neuro_workspace.services.jobs import execute_processing_job

    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)
    recording = client.get(f"/api/recordings/{recording_id}").json()
    active_version_id = recording["active_version_id"]

    runtime = client.app.state.runtime
    # Simulate a corrupted/incomplete canonical object: the DB row for this
    # version is intact, but its Zarr data is gone -- e.g. a partially
    # applied filesystem operation, or the shared-POSIX-volume bug class the
    # object-storage migration (Phase 3) is meant to close off entirely.
    runtime.signal_store.delete(active_version_id)

    factory = runtime.session_factory
    session = factory()
    try:
        job = Job(
            workspace_id=recording["workspace_id"],
            kind=JobKind.PROCESSING,
            subject_type="recording",
            subject_id=recording_id,
            idempotency_key=f"process:{recording_id}:corrupted-input",
            status=JobState.QUEUED,
        )
        session.add(job)
        session.flush()
        run = ProcessingRun(
            recording_id=recording_id,
            input_version_id=active_version_id,
            creator_id=session.query(User).first().id,
            job_id=job.id,
            operation_id="average_reference",
            operation_version="1",
            parameters={"channel_indices": [0]},
        )
        session.add(run)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    # The point under test is that _error_report() handles whatever the
    # storage layer happens to raise for missing Zarr data -- FileNotFoundError
    # today, but the executor's contract is "any exception fails the job with
    # structured metadata," not one specific exception type.
    with pytest.raises(Exception):  # noqa: B017
        execute_processing_job(
            factory=runtime.session_factory,
            signal_store=runtime.signal_store,
            settings=runtime.settings,
            job_id=job_id,
        )

    session = factory()
    try:
        job = session.get(Job, job_id)
        assert job.status == JobState.FAILED
        assert job.diagnostic_id is not None
        assert job.error_code is not None
    finally:
        session.close()
