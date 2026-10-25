"""Idempotent job execution shared by eager tests and Celery workers."""

from __future__ import annotations

import hashlib
import hmac
import tempfile
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from neuro_workspace.adapters.registry import AdapterRegistry
from neuro_workspace.config import Settings
from neuro_workspace.db.models import (
    Annotation,
    BadChannel,
    Channel,
    Event,
    Export,
    Job,
    ProcessingRun,
    QCFlag,
    QCMetric,
    Recording,
    RecordingVersion,
    SourceAsset,
    Upload,
)
from neuro_workspace.domain import AssetProbe, CSVImportConfig, JobState, RecordingState
from neuro_workspace.errors import NeuroError
from neuro_workspace.ingestion import ingest_recording
from neuro_workspace.metrics import (
    JOB_LEASES_RECLAIMED_TOTAL,
    JOBS_DEAD_LETTERED_TOTAL,
    JOBS_FAILED_TOTAL,
    JOBS_REDISPATCHED_TOTAL,
    JOBS_STARTED_TOTAL,
    JOBS_SUCCEEDED_TOTAL,
)
from neuro_workspace.processing import average_reference
from neuro_workspace.product_analytics import format_category, size_bucket, track_product_event
from neuro_workspace.signal_store import ZarrSignalStore
from neuro_workspace.storage import ObjectStore

logger = structlog.get_logger(__name__)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_error(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, NeuroError):
        return exc.code, exc.message
    return (
        "internal_error",
        "Processing failed unexpectedly. Retry, or contact support with the job ID.",
    )


def _error_report(exc: BaseException) -> tuple[str, str, str, list[dict[str, str | int | None]]]:
    """Common structured-failure metadata shared by every job executor.

    A stable (code, diagnostic_id) pair is what support/observability can
    actually act on without exposing internals: the code and message are
    safe to show a user, the diagnostic_id correlates a report to the full
    (server-side-only) traceback, and safe_trace is a bounded, filename-only
    slice of it -- enough to debug from logs without leaking local paths or
    variable values that could contain recording content.
    """

    code, message = _safe_error(exc)
    diagnostic_id = str(uuid.uuid4())
    safe_trace = [
        {"module": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
        for frame in traceback.extract_tb(exc.__traceback__)[-12:]
    ]
    return code, message, diagnostic_id, safe_trace


DEFAULT_LEASE_SECONDS = 15 * 60
UNDISPATCHED_GRACE_SECONDS = 30
# A QUEUED job whose message was handed to the broker this long ago without
# any worker claiming it is assumed lost (e.g. Redis restarted without its
# queue). Resending is safe because claim_job refuses duplicate deliveries;
# the interval only bounds how often a merely backlogged job gets a duplicate.
STALE_DISPATCH_SECONDS = 10 * 60


def claim_job(
    session: Session, job_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> tuple[bool, Job | None]:
    """Atomically transition a job to RUNNING for this worker, or refuse.

    The queue (Celery/Redis) is only at-least-once: the same message can be
    delivered twice, and two worker processes can both call this for the same
    job_id concurrently. Correctness therefore cannot live in application-code
    read-then-write; it has to be a single conditional UPDATE so the database
    picks exactly one winner. A row matches only if it is QUEUED, or RUNNING
    with an expired lease (a crashed worker never got to finish). Everyone
    else -- including a worker that is still legitimately holding the lease,
    or a job that already reached a terminal state -- gets rowcount 0 and
    must not do any work.
    """

    now = datetime.now(UTC)
    result = session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            or_(
                Job.status == JobState.QUEUED,
                and_(Job.status == JobState.RUNNING, Job.lease_expires_at < now),
            ),
        )
        .values(
            status=JobState.RUNNING,
            attempt=Job.attempt + 1,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        # This is a deliberate database-level compare-and-swap: the caller
        # always re-fetches via session.get() afterward, so ORM
        # session-synchronization is not needed. It also must be disabled --
        # the default "evaluate" strategy re-runs this WHERE clause in
        # Python against any copy of the row already in the identity map,
        # which raises on tz-aware/naive datetime comparisons under SQLite
        # and would silently diverge from what the database actually matched.
        .execution_options(synchronize_session=False)
    )
    claimed = result.rowcount == 1  # type: ignore[attr-defined]
    session.commit()
    # session_factory() in this app uses expire_on_commit=False, so without
    # this the identity map would keep serving whatever stale copy of the row
    # it already held (e.g. from the SELECT the caller used to find job_id) --
    # synchronize_session=False intentionally does not patch it in Python, so
    # this session-level expire is what gives the caller read-your-writes.
    session.expire_all()
    job = session.get(Job, job_id)
    if claimed and job is not None:
        JOBS_STARTED_TOTAL.labels(kind=job.kind.value).inc()
    return claimed, job


def heartbeat_job(
    session: Session, job_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> None:
    """Extend a held lease so a slow-but-alive job is not reclaimed mid-flight."""

    now = datetime.now(UTC)
    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobState.RUNNING)
        .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_seconds))
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.expire_all()


class LeaseHeartbeat:
    """Keeps a job's lease alive for the duration of one long blocking call.

    claim_job() only extends the lease at state-transition checkpoints
    (start, progress updates around commits); parsing a multi-gigabyte
    recording or running a processing algorithm can block far longer than
    one lease window without ever returning control to a point where
    heartbeat_job() could be called inline. This runs it on a fixed interval
    from a daemon thread with its own database session -- SQLAlchemy
    sessions are not thread-safe, so the heartbeat must never share the
    caller's session -- for the lifetime of a `with` block.
    """

    def __init__(
        self,
        factory: sessionmaker[Session],
        job_id: str,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        interval_seconds: float | None = None,
    ) -> None:
        self._factory = factory
        self._job_id = job_id
        self._lease_seconds = lease_seconds
        self._interval = interval_seconds or max(5.0, lease_seconds / 3)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            session = self._factory()
            try:
                heartbeat_job(session, self._job_id, lease_seconds=self._lease_seconds)
            except Exception:
                logger.warning("heartbeat_failed", job_id=self._job_id)
            finally:
                session.close()

    def __enter__(self) -> LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def request_cancellation(session: Session, job_id: str) -> Job | None:
    """Cancel a job: outright if it is still QUEUED, cooperatively otherwise.

    A QUEUED job has no worker running it, so it can be cancelled
    immediately. A RUNNING job's worker owns it and may be mid-syscall
    inside a C extension (MNE/Zarr); there is no safe way to interrupt it
    from here, so this only records the request. The executor checks
    is_cancel_requested() at safe checkpoints and finalizes to CANCELLED
    itself, cleaning up any partial output it produced.
    """

    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobState.QUEUED)
        .values(status=JobState.CANCELLED, stage="Cancelled")
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobState.RUNNING)
        .values(status=JobState.CANCEL_REQUESTED, stage="Cancellation requested")
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.expire_all()
    return session.get(Job, job_id)


def is_cancel_requested(session: Session, job_id: str) -> bool:
    """Cheap, session-fresh check for a cooperative cancellation checkpoint."""

    status = session.scalar(select(Job.status).where(Job.id == job_id))
    return status == JobState.CANCEL_REQUESTED


def mark_dispatched(session: Session, job_id: str) -> None:
    """Record that the job was handed to the broker (or run eagerly)."""

    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.dispatched_at.is_(None))
        .values(dispatched_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.expire_all()


def find_undispatched_jobs(
    session: Session, *, grace_seconds: int = UNDISPATCHED_GRACE_SECONDS, limit: int = 100
) -> list[Job]:
    """Jobs committed to the database but never confirmed sent to the broker.

    This is the read side of the transactional-outbox pattern: the Job row
    itself is the outbox entry (status=QUEUED, dispatched_at=NULL means
    "not yet delivered"), so there is no separate outbox table to keep in
    sync. The grace period avoids racing the normal synchronous dispatch that
    happens milliseconds after the same commit.
    """

    threshold = datetime.now(UTC) - timedelta(seconds=grace_seconds)
    return list(
        session.scalars(
            select(Job)
            .where(
                Job.status == JobState.QUEUED,
                Job.dispatched_at.is_(None),
                Job.created_at < threshold,
            )
            .order_by(Job.created_at)
            .limit(limit)
        )
    )


def find_stale_dispatched_jobs(
    session: Session, *, stale_seconds: int = STALE_DISPATCH_SECONDS, limit: int = 100
) -> list[Job]:
    """Jobs handed to the broker that no worker has claimed in `stale_seconds`.

    The outbox only records that a message was sent, not that it survived:
    if the broker loses its queue after dispatch, the job would otherwise sit
    in QUEUED with dispatched_at set and never be selected again.
    """

    threshold = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    return list(
        session.scalars(
            select(Job)
            .where(
                Job.status == JobState.QUEUED,
                Job.dispatched_at.is_not(None),
                Job.dispatched_at < threshold,
            )
            .order_by(Job.dispatched_at)
            .limit(limit)
        )
    )


def mark_redispatched(session: Session, job_id: str) -> None:
    """Restart the stale-dispatch clock for a job that was just resent."""

    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobState.QUEUED)
        .values(dispatched_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.expire_all()


def run_dispatch_sweep(
    *,
    factory: sessionmaker[Session],
    redispatch: Callable[[str, str], None],
) -> dict[str, int]:
    """One pass of the outbox dispatcher and lease-reclaim sweep.

    `redispatch(job_id, kind)` must hand the job to the broker (or raise);
    injected rather than imported so this stays testable without a running
    Celery/Redis broker, and so the API-eager code path and the Celery-beat
    code path can share the exact same reclaim logic.
    """

    session = factory()
    try:
        reclaimed_ids, dead_letter_ids = reclaim_expired_leases(session)
        pending = find_undispatched_jobs(session)
        stale = find_stale_dispatched_jobs(session)
        redispatched = 0
        for job, mark in [(job, mark_dispatched) for job in pending] + [
            (job, mark_redispatched) for job in stale
        ]:
            try:
                redispatch(job.id, job.kind.value)
            except Exception:
                logger.warning("job_redispatch_failed", job_id=job.id, kind=job.kind.value)
                continue
            mark(session, job.id)
            redispatched += 1
            JOBS_REDISPATCHED_TOTAL.inc()
        return {
            "leases_reclaimed": len(reclaimed_ids),
            "dead_lettered": len(dead_letter_ids),
            "redispatched": redispatched,
            "pending_considered": len(pending),
            "stale_considered": len(stale),
        }
    finally:
        session.close()


def reclaim_expired_leases(session: Session, *, limit: int = 200) -> tuple[list[str], list[str]]:
    """Reset RUNNING jobs whose lease expired back to QUEUED, or dead-letter them.

    A worker that crashes mid-job stops heartbeating; its lease eventually
    expires. Reclaiming to QUEUED (with dispatched_at cleared, so the outbox
    sweep redrives it) lets a different worker pick the job back up via the
    same claim_job() race used everywhere else -- no separate crash-recovery
    code path to keep correct. A job that has already exhausted max_attempts
    is dead-lettered (FAILED with a stable error_code) instead of retried
    forever.
    """

    now = datetime.now(UTC)
    candidate_ids = list(
        session.scalars(
            select(Job.id)
            .where(
                Job.status == JobState.RUNNING,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at < now,
            )
            .limit(limit)
        )
    )
    if not candidate_ids:
        return [], []
    # The candidate SELECT above is not locking, so it is only a hint of
    # what to look at -- found live: two overlapping sweeps (worker-beat
    # catching up after a gap) can both see the same job as an expired-lease
    # candidate before either commits. Each UPDATE below re-states the exact
    # same status/lease_expires_at condition as the SELECT, so it is the
    # actual compare-and-swap: only whichever UPDATE runs first can still
    # match a row that has already been reclaimed by the other, and the
    # loser's `id IN (...)` matches zero rows for that id instead of
    # double-reclaiming it and double-counting the metric.
    reclaimed = session.execute(
        update(Job)
        .where(
            Job.id.in_(candidate_ids),
            Job.status == JobState.RUNNING,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < now,
            Job.attempt < Job.max_attempts,
        )
        .values(
            status=JobState.QUEUED,
            dispatched_at=None,
            stage="Reclaimed after lease expiry",
            lease_expires_at=None,
        )
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    )
    reclaimed_ids = [str(row[0]) for row in reclaimed]
    dead_lettered = session.execute(
        update(Job)
        .where(
            Job.id.in_(candidate_ids),
            Job.status == JobState.RUNNING,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < now,
            Job.attempt >= Job.max_attempts,
        )
        .values(
            status=JobState.FAILED,
            error_code="lease_expired_max_attempts",
            error_message=(
                "The worker lease expired repeatedly without the job completing. "
                "It will not be retried automatically."
            ),
            stage="Failed",
        )
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    )
    dead_letter_ids = [str(row[0]) for row in dead_lettered]
    session.commit()
    session.expire_all()
    if reclaimed_ids:
        JOB_LEASES_RECLAIMED_TOTAL.inc(len(reclaimed_ids))
    if dead_letter_ids:
        JOBS_DEAD_LETTERED_TOTAL.inc(len(dead_letter_ids))
    return reclaimed_ids, dead_letter_ids


def _begin_job(session: Session, job_id: str, recording_id: str) -> tuple[Job, Recording] | None:
    claimed, job = claim_job(session, job_id)
    recording = session.get(Recording, recording_id)
    if job is None or recording is None:
        raise RuntimeError("job subject is missing")
    if not claimed:
        # Either a terminal state (already SUCCEEDED/CANCELLED -- re-entry is a
        # no-op), or another worker currently holds a valid lease on this job.
        # Neither case should touch recording/version state from here.
        return None
    if recording.state in {RecordingState.DELETING, RecordingState.DELETED}:
        job.status = JobState.CANCELLED
        job.stage = "Cancelled"
        session.commit()
        return None
    job.progress = 0.03
    job.stage = "Inspecting"
    recording.state = RecordingState.INGESTING
    session.commit()
    return job, recording


def execute_ingestion_job(
    *,
    factory: sessionmaker[Session],
    object_store: ObjectStore,
    signal_store: ZarrSignalStore,
    registry: AdapterRegistry,
    settings: Settings,
    job_id: str,
) -> None:
    """Execute one ingestion job; re-entry after success is a no-op."""

    session = factory()
    recording_id = ""
    adapter_id = "unknown"
    failed_recording: Recording | None = None
    version_id = str(uuid.uuid4())
    try:
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError("job is missing")
        recording_id = job.subject_id
        claimed = _begin_job(session, job_id, recording_id)
        if claimed is None:
            return
        job, recording = claimed
        assets = list(
            session.scalars(
                select(SourceAsset)
                .where(SourceAsset.recording_id == recording.id)
                .order_by(SourceAsset.created_at, SourceAsset.id)
            )
        )
        upload = session.get(Upload, assets[0].upload_id) if assets else None
        if not assets or upload is None:
            raise RuntimeError("recording source assets are missing")

        with tempfile.TemporaryDirectory(prefix=f"nw-{job.id[:8]}-") as temp_name:
            temp = Path(temp_name)
            paths: list[Path] = []
            source_hashes: list[str] = []
            for asset in assets:
                if is_cancel_requested(session, job_id):
                    signal_store.delete(version_id)
                    job.status = JobState.CANCELLED
                    job.stage = "Cancelled"
                    session.commit()
                    return
                destination = temp / Path(asset.safe_name).name
                object_store.copy_to_path(asset.object_key, destination)
                digest = _sha256_file(destination)
                if asset.sha256 is not None and not hmac.compare_digest(asset.sha256, digest):
                    raise NeuroError(
                        "checksum_mismatch",
                        f"Integrity verification failed for {asset.safe_name}. "
                        "Upload the file again.",
                        422,
                    )
                asset.sha256 = digest
                asset.size_bytes = destination.stat().st_size
                paths.append(destination)
                source_hashes.append(digest)
            probes = [
                AssetProbe(asset.safe_name, str(path), asset.size_bytes, asset.role)
                for asset, path in zip(assets, paths, strict=True)
            ]
            adapter = (
                registry.get(upload.format_hint) if upload.format_hint else registry.detect(probes)
            )
            adapter_id = adapter.format_id
            if not recording.is_demo:
                track_product_event(
                    session,
                    "ingestion_started",
                    actor_id=recording.creator_id,
                    workspace_id=recording.workspace_id,
                    recording_id=recording.id,
                    properties={
                        "format": format_category(upload.format_hint or adapter_id),
                        "size_bucket": size_bucket(upload.total_bytes),
                    },
                )
            config = CSVImportConfig(**upload.csv_config) if upload.csv_config else None
            job.progress = 0.12
            job.stage = "Processing"
            recording.state = RecordingState.PROCESSING
            session.commit()
            # Parsing a multi-gigabyte recording can run well past one lease
            # window; keep the lease alive from a side thread for the
            # duration of this single blocking call. See LeaseHeartbeat.
            with LeaseHeartbeat(factory, job_id):
                result = ingest_recording(
                    adapter=adapter,
                    assets=paths,
                    import_config=config,
                    version_id=version_id,
                    signal_store=signal_store,
                    source_hashes=tuple(source_hashes),
                    build_version=settings.build_version,
                )

        # A deletion (or explicit cancellation) request that arrived during
        # parsing must win before publication -- an in-flight ingestion must
        # never surface a RecordingVersion for a recording the user asked to
        # delete, or a job they asked to cancel.
        session.refresh(recording)
        if recording.state in {RecordingState.DELETING, RecordingState.DELETED} or (
            is_cancel_requested(session, job_id)
        ):
            signal_store.delete(version_id)
            job.status = JobState.CANCELLED
            job.stage = "Cancelled"
            session.commit()
            return

        version = RecordingVersion(
            id=version_id,
            recording_id=recording.id,
            kind="normalized_raw",
            store_key=version_id,
            manifest_sha256=signal_store.manifest_sha256(version_id),
            sampling_frequency_hz=result.metadata.sampling_frequency_hz,
            sample_count=result.metadata.sample_count,
            duration_seconds=result.metadata.duration_seconds,
            channel_count=len(result.metadata.channels),
            software=result.manifest.software,
            ready_at=datetime.now(UTC),
        )
        session.add(version)
        # These models intentionally avoid ORM relationships. Flush the parent
        # explicitly so strict foreign-key engines never schedule children first.
        session.flush()
        channel_rows: list[Channel] = []
        for channel in result.metadata.channels:
            row = Channel(
                version_id=version.id,
                channel_index=channel.index,
                name=channel.name,
                source_name=channel.source_name,
                channel_type=channel.channel_type,
                unit=channel.unit,
                source_unit=channel.source_unit,
                conversion_to_si=channel.conversion_to_si,
                source_sampling_frequency_hz=channel.source_sampling_frequency_hz,
            )
            session.add(row)
            channel_rows.append(row)
        session.flush()
        for event in result.metadata.events:
            session.add(
                Event(
                    version_id=version.id,
                    onset_seconds=event.onset_seconds,
                    duration_seconds=event.duration_seconds,
                    label=event.label[:255],
                    code=event.code,
                    source_index=event.source_index,
                )
            )
        for channel_qc, row_channel in zip(result.qc, channel_rows, strict=True):
            metrics = {
                "finite_fraction": (channel_qc.finite_fraction, "1"),
                "standard_deviation": (channel_qc.standard_deviation_si, "V"),
                "maximum_absolute": (channel_qc.maximum_absolute_si, "V"),
                "line_noise_ratio": (channel_qc.line_noise_ratio, "1"),
            }
            metric_rows: dict[str, QCMetric] = {}
            for rule_id, (value, unit) in metrics.items():
                metric = QCMetric(
                    version_id=version.id,
                    channel_id=row_channel.id,
                    rule_id=rule_id,
                    rule_version="1",
                    value=value,
                    unit=unit,
                    coverage={
                        "start_seconds": 0,
                        "duration_seconds": min(300, result.metadata.duration_seconds),
                    },
                )
                session.add(metric)
                metric_rows[rule_id] = metric
            session.flush()
            flags = [
                (channel_qc.flat, "standard_deviation", "Near-flat signal; review this channel."),
                (
                    channel_qc.high_amplitude,
                    "maximum_absolute",
                    "High absolute amplitude; review this channel.",
                ),
                (
                    channel_qc.variance_outlier,
                    "standard_deviation",
                    "Variance differs strongly from peer channels.",
                ),
                (
                    channel_qc.dropout,
                    "finite_fraction",
                    "A long repeated-value run suggests possible dropout.",
                ),
                (
                    channel_qc.discontinuity,
                    "maximum_absolute",
                    "A large discontinuity was detected.",
                ),
                (
                    channel_qc.excessive_line_noise,
                    "line_noise_ratio",
                    "Line-frequency energy is elevated.",
                ),
                (
                    channel_qc.finite_fraction < 1.0,
                    "finite_fraction",
                    "Missing or non-finite samples are present.",
                ),
            ]
            for active, metric_name, message in flags:
                if active:
                    session.add(
                        QCFlag(
                            metric_id=metric_rows[metric_name].id,
                            severity="review",
                            safe_message=message,
                        )
                    )
        recording.format_id = result.metadata.format_id
        recording.safe_metadata = {
            "sampling_frequency_hz": result.metadata.sampling_frequency_hz,
            "sample_count": result.metadata.sample_count,
            "duration_seconds": result.metadata.duration_seconds,
            "channel_count": len(result.metadata.channels),
            "event_count": len(result.metadata.events),
            "warnings": list(result.metadata.warnings),
        }
        recording.source_provenance = {
            "assets": [{"role": asset.role.value, "sha256": asset.sha256} for asset in assets],
            "adapter": result.metadata.format_id,
            "software": result.manifest.software,
        }
        recording.active_version_id = version.id
        recording.state = RecordingState.READY
        recording.error_code = None
        recording.error_message = None
        job.status = JobState.SUCCEEDED
        job.progress = 1.0
        job.stage = "Ready"
        JOBS_SUCCEEDED_TOTAL.labels(kind="INGESTION").inc()
        if not recording.is_demo:
            track_product_event(
                session,
                "ingestion_succeeded",
                actor_id=recording.creator_id,
                workspace_id=recording.workspace_id,
                recording_id=recording.id,
                properties={
                    "format": format_category(recording.format_id),
                    "size_bucket": size_bucket(upload.total_bytes),
                },
            )
        session.commit()
    except BaseException as exc:
        session.rollback()
        if version_id:
            signal_store.delete(version_id)
        code, message, diagnostic_id, safe_trace = _error_report(exc)
        if recording_id:
            failed_recording = session.get(Recording, recording_id)
            if failed_recording is not None and failed_recording.state not in {
                RecordingState.DELETING,
                RecordingState.DELETED,
            }:
                failed_recording.state = RecordingState.FAILED
                failed_recording.error_code = code
                failed_recording.error_message = message
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobState.FAILED
            job.error_code = code
            job.error_message = message
            job.stage = "Failed"
            job.diagnostic_id = diagnostic_id
            JOBS_FAILED_TOTAL.labels(kind="INGESTION").inc()
        failed_upload = None
        if recording_id:
            failed_asset = session.scalar(
                select(SourceAsset).where(SourceAsset.recording_id == recording_id).limit(1)
            )
            failed_upload = session.get(Upload, failed_asset.upload_id) if failed_asset else None
        if failed_recording is not None and not failed_recording.is_demo:
            track_product_event(
                session,
                "ingestion_failed",
                actor_id=failed_recording.creator_id,
                workspace_id=failed_recording.workspace_id,
                recording_id=failed_recording.id,
                properties={
                    "format": format_category(
                        (failed_upload.format_hint if failed_upload else None) or adapter_id
                    ),
                    "size_bucket": size_bucket(
                        failed_upload.total_bytes if failed_upload else None
                    ),
                    "failure_class": type(exc).__name__,
                },
            )
        logger.error(
            "ingestion_failed",
            diagnostic_id=diagnostic_id,
            job_id=job_id,
            recording_id=recording_id,
            adapter=adapter_id,
            format=format_category(failed_upload.format_hint if failed_upload else None),
            build_version=settings.build_version,
            failure_class=type(exc).__name__,
            safe_trace=safe_trace,
        )
        session.commit()
        raise
    finally:
        session.close()


def execute_processing_job(
    *,
    factory: sessionmaker[Session],
    signal_store: ZarrSignalStore,
    settings: Settings,
    job_id: str,
) -> None:
    session = factory()
    output_version_id = str(uuid.uuid4())
    try:
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError("job is missing")
        claimed, job = claim_job(session, job_id)
        if not claimed:
            return
        assert job is not None  # claim_job only returns claimed=True for a row it found
        run = session.scalar(select(ProcessingRun).where(ProcessingRun.job_id == job.id))
        if run is None:
            raise RuntimeError("processing run is missing")
        recording = session.get(Recording, run.recording_id)
        if recording is None or recording.state != RecordingState.READY:
            raise NeuroError(
                "recording_not_ready", "The recording is not ready for processing.", 409
            )
        job.progress = 0.1
        job.stage = "Processing"
        run.status = "RUNNING"
        session.commit()
        if run.operation_id != "average_reference":
            raise NeuroError(
                "unsupported_operation", "This processing operation is not supported.", 422
            )
        indices = [int(value) for value in run.parameters.get("channel_indices", [])]
        with LeaseHeartbeat(factory, job_id):
            result = average_reference(
                signal_store=signal_store,
                input_version_id=run.input_version_id,
                output_version_id=output_version_id,
                reference_channel_indices=indices,
                build_version=settings.build_version,
            )
        input_version = session.get(RecordingVersion, run.input_version_id)
        if input_version is None:
            raise RuntimeError("input version is missing")
        session.refresh(recording)
        if recording.state in {
            RecordingState.DELETING,
            RecordingState.DELETED,
        } or is_cancel_requested(session, job_id):
            signal_store.delete(output_version_id)
            job.status = JobState.CANCELLED
            run.status = "CANCELLED"
            session.commit()
            return
        version = RecordingVersion(
            id=output_version_id,
            recording_id=recording.id,
            parent_version_id=input_version.id,
            kind="derived",
            store_key=output_version_id,
            manifest_sha256=signal_store.manifest_sha256(output_version_id),
            sampling_frequency_hz=result.metadata.sampling_frequency_hz,
            sample_count=result.metadata.sample_count,
            duration_seconds=result.metadata.duration_seconds,
            channel_count=len(result.metadata.channels),
            software=result.manifest.software,
            ready_at=datetime.now(UTC),
        )
        session.add(version)
        session.flush()
        for channel in result.metadata.channels:
            session.add(
                Channel(
                    version_id=version.id,
                    channel_index=channel.index,
                    name=channel.name,
                    source_name=channel.source_name,
                    channel_type=channel.channel_type,
                    unit=channel.unit,
                    source_unit=channel.source_unit,
                    conversion_to_si=channel.conversion_to_si,
                    source_sampling_frequency_hz=channel.source_sampling_frequency_hz,
                )
            )
        for event in result.metadata.events:
            session.add(
                Event(
                    version_id=version.id,
                    onset_seconds=event.onset_seconds,
                    duration_seconds=event.duration_seconds,
                    label=event.label[:255],
                    code=event.code,
                    source_index=event.source_index,
                )
            )
        run.output_version_id = version.id
        run.status = "SUCCEEDED"
        # Same convention ingestion uses (see ingest_recording): the
        # recording always points at its latest ready version. The parent
        # version is untouched and still reachable via
        # RecordingVersion.parent_version_id / the provenance export -- this
        # repoints "what the viewer shows for this recording," it does not
        # mutate the input version.
        recording.active_version_id = version.id
        job.status = JobState.SUCCEEDED
        job.progress = 1.0
        job.stage = "Ready"
        JOBS_SUCCEEDED_TOTAL.labels(kind="PROCESSING").inc()
        session.commit()
    except BaseException as exc:
        session.rollback()
        signal_store.delete(output_version_id)
        code, message, diagnostic_id, safe_trace = _error_report(exc)
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobState.FAILED
            job.stage = "Failed"
            job.error_code = code
            job.error_message = message
            job.diagnostic_id = diagnostic_id
            JOBS_FAILED_TOTAL.labels(kind="PROCESSING").inc()
        run = session.scalar(select(ProcessingRun).where(ProcessingRun.job_id == job_id))
        if run is not None:
            run.status = "FAILED"
        logger.error(
            "processing_failed",
            diagnostic_id=diagnostic_id,
            job_id=job_id,
            operation_id=run.operation_id if run is not None else None,
            build_version=settings.build_version,
            failure_class=type(exc).__name__,
            safe_trace=safe_trace,
        )
        session.commit()
        raise
    finally:
        session.close()


def execute_deletion_job(
    *,
    factory: sessionmaker[Session],
    object_store: ObjectStore,
    signal_store: ZarrSignalStore,
    job_id: str,
) -> None:
    session = factory()
    try:
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError("job is missing")
        recording = session.get(Recording, job.subject_id)
        if recording is None:
            job.status = JobState.SUCCEEDED
            session.commit()
            return
        claimed, job = claim_job(session, job_id)
        if not claimed:
            return
        assert job is not None
        job.stage = "Deleting"
        session.commit()
        version_ids = list(
            session.scalars(
                select(RecordingVersion.id).where(RecordingVersion.recording_id == recording.id)
            )
        )
        for version_id in version_ids:
            signal_store.delete(version_id)
        exports = list(session.scalars(select(Export).where(Export.recording_id == recording.id)))
        for export in exports:
            if export.object_key:
                object_store.delete_prefix(export.object_key)
        session.execute(delete(Export).where(Export.recording_id == recording.id))
        session.execute(delete(Annotation).where(Annotation.recording_id == recording.id))
        session.execute(delete(BadChannel).where(BadChannel.recording_id == recording.id))
        metric_ids = select(QCMetric.id).where(QCMetric.version_id.in_(version_ids))
        session.execute(delete(QCFlag).where(QCFlag.metric_id.in_(metric_ids)))
        session.execute(delete(QCMetric).where(QCMetric.version_id.in_(version_ids)))
        session.execute(delete(Event).where(Event.version_id.in_(version_ids)))
        session.execute(delete(Channel).where(Channel.version_id.in_(version_ids)))
        session.execute(delete(ProcessingRun).where(ProcessingRun.recording_id == recording.id))
        assets = list(
            session.scalars(select(SourceAsset).where(SourceAsset.recording_id == recording.id))
        )
        upload_ids = {asset.upload_id for asset in assets}
        for asset in assets:
            object_store.delete_prefix(asset.object_key)
            session.delete(asset)
        recording.active_version_id = None
        session.flush()
        # Derived versions reference their parents, so remove leaves before roots.
        session.execute(
            delete(RecordingVersion).where(
                RecordingVersion.recording_id == recording.id,
                RecordingVersion.parent_version_id.is_not(None),
            )
        )
        session.execute(
            delete(RecordingVersion).where(RecordingVersion.recording_id == recording.id)
        )
        session.execute(delete(Upload).where(Upload.id.in_(upload_ids)))
        recording.safe_metadata = {}
        recording.source_provenance = {}
        recording.state = RecordingState.DELETED
        recording.deleted_at = recording.deleted_at or datetime.now(UTC)
        job.status = JobState.SUCCEEDED
        job.progress = 1.0
        job.stage = "Deleted"
        JOBS_SUCCEEDED_TOTAL.labels(kind="DELETION").inc()
        session.commit()
    except BaseException as exc:
        session.rollback()
        code, message, diagnostic_id, safe_trace = _error_report(exc)
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobState.FAILED
            job.error_code = code
            job.error_message = message
            JOBS_FAILED_TOTAL.labels(kind="DELETION").inc()
            job.diagnostic_id = diagnostic_id
        logger.error(
            "deletion_failed",
            diagnostic_id=diagnostic_id,
            job_id=job_id,
            failure_class=type(exc).__name__,
            safe_trace=safe_trace,
        )
        session.commit()
        raise
    finally:
        session.close()
