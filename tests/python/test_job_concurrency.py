"""Correctness tests for atomic job claiming, lease reclamation, and the
transactional-outbox dispatch sweep.

These exercise services.jobs directly against a real (sqlite) database
session rather than mocking the ORM, because the property under test -- that
a conditional UPDATE picks exactly one winner -- only means something against
real transactional semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from neuro_workspace.config import Settings
from neuro_workspace.db.models import Base, Job, Workspace
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.domain import JobKind, JobState
from neuro_workspace.services.jobs import (
    claim_job,
    find_undispatched_jobs,
    mark_dispatched,
    reclaim_expired_leases,
    run_dispatch_sweep,
)


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


def test_only_one_claimant_wins_a_fresh_job(factory) -> None:
    """Simulates duplicate Celery delivery: two 'workers' race claim_job()."""

    job_id = _make_job(factory)
    session_a = factory()
    session_b = factory()
    try:
        claimed_a, job_a = claim_job(session_a, job_id)
        claimed_b, job_b = claim_job(session_b, job_id)

        assert claimed_a is True
        assert claimed_b is False
        assert job_a is not None and job_a.status == JobState.RUNNING
        assert job_a.attempt == 1
        # The loser must not have incremented attempt or reset the lease.
        assert job_b is not None and job_b.attempt == 1
    finally:
        session_a.close()
        session_b.close()


def test_running_job_with_live_lease_cannot_be_reclaimed(factory) -> None:
    job_id = _make_job(factory)
    session = factory()
    try:
        claimed, _ = claim_job(session, job_id, lease_seconds=900)
        assert claimed is True
        # A second claimant while the lease is still valid must lose.
        claimed_again, _ = claim_job(session, job_id, lease_seconds=900)
        assert claimed_again is False
    finally:
        session.close()


def test_expired_lease_is_reclaimed_and_then_claimable(factory) -> None:
    job_id = _make_job(factory)
    session = factory()
    try:
        claim_job(session, job_id, lease_seconds=900)
        # Simulate a crashed worker: force the lease into the past directly.
        job = session.get(Job, job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

        reclaimed_ids, dead_letter_ids = reclaim_expired_leases(session)
        assert reclaimed_ids == [job_id]
        assert dead_letter_ids == []

        job = session.get(Job, job_id)
        assert job.status == JobState.QUEUED
        assert job.dispatched_at is None  # must be redriven by the outbox sweep

        claimed, _ = claim_job(session, job_id)
        assert claimed is True
    finally:
        session.close()


def test_concurrent_reclaim_sweeps_do_not_double_reclaim_the_same_job(factory) -> None:
    """Found by actually running the worker-beat sweep against a live
    stack: two overlapping `reclaim_jobs` executions (worker-beat catching
    up after a gap following a real worker crash) both selected the same
    expired-lease job as a candidate before either committed, and -- before
    the fix -- both UPDATEs still matched it because the write only
    filtered on `id IN (...)`, not the same status/lease condition the read
    used. Live Postgres logs showed both concurrent sweeps reporting
    `leases_reclaimed: 1` for the one job.

    reclaim_expired_leases() does its SELECT and UPDATE back-to-back inside
    one call, so calling it twice in sequence can never reproduce the race
    (the second call's own SELECT would simply not find an already-QUEUED
    job, bug or no bug). To actually exercise the fix, this manually
    interleaves two sessions' candidate SELECTs -- both capture the job as
    RUNNING+expired -- before either UPDATE runs, the same shape a real
    concurrent overlap has.
    """

    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    job_id = _make_job(factory)
    session_a = factory()
    session_b = factory()
    try:
        claim_job(session_a, job_id, lease_seconds=900)
        job = session_a.get(Job, job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session_a.commit()

        now = datetime.now(UTC)
        candidate_query = sa_select(Job.id).where(
            Job.status == JobState.RUNNING,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < now,
        )
        # Both sessions see the job as a candidate before either writes --
        # this is the interleaving that happened live.
        candidates_a = list(session_a.scalars(candidate_query))
        candidates_b = list(session_b.scalars(candidate_query))
        assert candidates_a == [job_id]
        assert candidates_b == [job_id]

        reclaim_stmt = (
            sa_update(Job)
            .where(
                Job.id.in_([job_id]),
                Job.status == JobState.RUNNING,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at < now,
                Job.attempt < Job.max_attempts,
            )
            .values(status=JobState.QUEUED, dispatched_at=None, lease_expires_at=None)
            .execution_options(synchronize_session=False)
        )
        result_a = session_a.execute(reclaim_stmt)
        session_a.commit()
        result_b = session_b.execute(reclaim_stmt)
        session_b.commit()

        assert result_a.rowcount == 1  # winner
        assert result_b.rowcount == 0  # loser: row no longer matches status=RUNNING
    finally:
        session_a.close()
        session_b.close()


def test_job_exhausting_attempts_is_dead_lettered_not_retried_forever(factory) -> None:
    job_id = _make_job(factory, max_attempts=1)
    session = factory()
    try:
        claim_job(session, job_id)  # attempt becomes 1, equal to max_attempts
        job = session.get(Job, job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

        reclaimed_ids, dead_letter_ids = reclaim_expired_leases(session)
        assert reclaimed_ids == []
        assert dead_letter_ids == [job_id]

        job = session.get(Job, job_id)
        assert job.status == JobState.FAILED
        assert job.error_code == "lease_expired_max_attempts"
    finally:
        session.close()


def test_undispatched_job_is_found_only_after_grace_period(factory) -> None:
    _make_job(factory)
    session = factory()
    try:
        # Freshly created: within the grace period, must not be surfaced yet
        # (it would otherwise race the synchronous dispatch that normally
        # follows the same commit within milliseconds).
        assert find_undispatched_jobs(session, grace_seconds=30) == []
        assert find_undispatched_jobs(session, grace_seconds=0) != []
    finally:
        session.close()


def test_mark_dispatched_is_idempotent(factory) -> None:
    job_id = _make_job(factory)
    session = factory()
    try:
        mark_dispatched(session, job_id)
        job = session.get(Job, job_id)
        first_timestamp = job.dispatched_at
        assert first_timestamp is not None

        mark_dispatched(session, job_id)
        session.refresh(job)
        assert job.dispatched_at == first_timestamp
    finally:
        session.close()


def test_dispatch_sweep_redrives_undispatched_and_reclaims_leases(factory) -> None:
    job_id = _make_job(factory)
    session = factory()
    try:
        job = session.get(Job, job_id)
        job.status = JobState.RUNNING
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        session.commit()
    finally:
        session.close()

    redispatched: list[tuple[str, str]] = []

    def _redispatch(job_id: str, kind: str) -> None:
        redispatched.append((job_id, kind))

    result = run_dispatch_sweep(factory=factory, redispatch=_redispatch)
    assert result["leases_reclaimed"] == 1
    # Reclaimed job becomes QUEUED with dispatched_at cleared, but is younger
    # than the default grace period, so this sweep should not redispatch it
    # yet -- only a *later* sweep (once the grace period elapses) would.
    assert redispatched == []


def test_dispatch_sweep_redispatch_failure_does_not_mark_dispatched(factory) -> None:
    job_id = _make_job(factory)
    session = factory()
    try:
        job = session.get(Job, job_id)
        job.created_at = datetime.now(UTC) - timedelta(seconds=60)
        session.commit()
    finally:
        session.close()

    def _failing_redispatch(job_id: str, kind: str) -> None:
        raise ConnectionError("broker still down")

    result = run_dispatch_sweep(factory=factory, redispatch=_failing_redispatch)
    assert result["redispatched"] == 0
    assert result["pending_considered"] == 1

    session = factory()
    try:
        job = session.get(Job, job_id)
        assert job.dispatched_at is None
    finally:
        session.close()
