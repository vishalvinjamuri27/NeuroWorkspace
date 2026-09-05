"""Recording, viewer, annotation, QC, processing, export, and deletion routes."""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import structlog
from celery import Celery
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from neuro_api.runtime import (
    Runtime,
    current_user,
    get_db,
    get_runtime,
    optional_identity,
)
from neuro_api.schemas import (
    AnnotationCreate,
    AnnotationDelete,
    AnnotationPage,
    AnnotationResponse,
    AnnotationUpdate,
    BadChannelResponse,
    BadChannelUpdate,
    ChannelResponse,
    EventResponse,
    ExportCreate,
    ExportResponse,
    JobResponse,
    ProcessingRunCreate,
    ProcessingRunResponse,
    RecordingPage,
    RecordingResponse,
)
from neuro_workspace.auth import Identity
from neuro_workspace.db.models import (
    Annotation,
    AuditEvent,
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
    User,
)
from neuro_workspace.domain import Capability, JobKind, RecordingState
from neuro_workspace.errors import ConflictError, ForbiddenError, ValidationError
from neuro_workspace.exports import export_annotations_csv, export_provenance_json
from neuro_workspace.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    encode_datetime_cursor,
    encode_float_cursor,
    paginate,
)
from neuro_workspace.product_analytics import track_product_event
from neuro_workspace.services.access import (
    ensure_user,
    job_for_user,
    project_for_user,
    recording_for_user,
)
from neuro_workspace.services.jobs import (
    execute_deletion_job,
    execute_processing_job,
    mark_dispatched,
    request_cancellation,
)

router = APIRouter()
logger = structlog.get_logger(__name__)


def _public_or_authorized_recording(
    session: Session,
    recording_id: str,
    identity: Identity | None,
    capability: Capability = Capability.READ,
) -> Recording:
    recording = session.get(Recording, recording_id)
    if recording is not None and recording.is_demo and recording.deleted_at is None:
        return recording
    if identity is None:
        raise ForbiddenError()
    user = ensure_user(
        session,
        subject=identity.subject,
        email=identity.email,
        display_name=identity.display_name,
    )
    session.commit()
    return recording_for_user(session, user.id, recording_id, capability)


def _active_version(recording: Recording) -> str:
    if recording.state != RecordingState.READY or recording.active_version_id is None:
        raise ConflictError("recording_not_ready", "The recording is not ready for viewing.")
    return recording.active_version_id


def _dispatch_processing(
    background: BackgroundTasks, runtime: Runtime, session: Session, job_id: str
) -> None:
    if runtime.settings.worker_eager:
        background.add_task(
            execute_processing_job,
            factory=runtime.session_factory,
            signal_store=runtime.signal_store,
            settings=runtime.settings,
            job_id=job_id,
        )
        mark_dispatched(session, job_id)
        return
    try:
        Celery(broker=runtime.settings.redis_url).send_task(
            "neuroworkspace.process", args=[job_id], queue="processing"
        )
    except Exception:
        logger.warning("job_dispatch_failed", job_id=job_id, kind="processing")
        return
    mark_dispatched(session, job_id)


def _dispatch_deletion(
    background: BackgroundTasks, runtime: Runtime, session: Session, job_id: str
) -> None:
    if runtime.settings.worker_eager:
        background.add_task(
            execute_deletion_job,
            factory=runtime.session_factory,
            object_store=runtime.object_store,
            signal_store=runtime.signal_store,
            job_id=job_id,
        )
        mark_dispatched(session, job_id)
        return
    try:
        Celery(broker=runtime.settings.redis_url).send_task(
            "neuroworkspace.delete", args=[job_id], queue="maintenance"
        )
    except Exception:
        logger.warning("job_dispatch_failed", job_id=job_id, kind="deletion")
        return
    mark_dispatched(session, job_id)


@router.get("/projects/{project_id}/recordings", response_model=RecordingPage)
def list_recordings(
    project_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> RecordingPage:
    project = project_for_user(session, user.id, project_id)
    stmt = (
        select(Recording)
        .where(Recording.project_id == project.id, Recording.deleted_at.is_(None))
        .order_by(Recording.created_at.desc(), Recording.id.desc())
    )
    rows, has_more = paginate(
        session,
        stmt,
        sort_column=Recording.created_at,
        id_column=Recording.id,
        cursor=cursor,
        limit=limit,
    )
    next_cursor = (
        encode_datetime_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    )
    return RecordingPage(
        items=[RecordingResponse.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.get("/demo/recording", response_model=RecordingResponse)
def get_demo_recording(session: Session = Depends(get_db)) -> RecordingResponse:
    recording = session.scalar(
        select(Recording)
        .where(Recording.is_demo.is_(True), Recording.state == RecordingState.READY)
        .order_by(Recording.created_at)
    )
    if recording is None:
        raise ConflictError(
            "demo_not_seeded", "The sample recording is not ready. Run the sample seed command."
        )
    return RecordingResponse.model_validate(recording)


@router.get("/recordings/{recording_id}", response_model=RecordingResponse)
def get_recording(
    recording_id: str,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
) -> RecordingResponse:
    recording = _public_or_authorized_recording(session, recording_id, identity)
    if identity is not None and not recording.is_demo:
        user = ensure_user(
            session,
            subject=identity.subject,
            email=identity.email,
            display_name=identity.display_name,
        )
        session.add(
            AuditEvent(
                workspace_id=recording.workspace_id,
                actor_id=user.id,
                action="viewer.opened",
                target_type="recording",
                target_id=recording.id,
            )
        )
        session.commit()
    return RecordingResponse.model_validate(recording)


@router.get("/recordings/{recording_id}/channels", response_model=list[ChannelResponse])
def list_channels(
    recording_id: str,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
) -> list[ChannelResponse]:
    recording = _public_or_authorized_recording(session, recording_id, identity)
    version_id = _active_version(recording)
    channels = session.scalars(
        select(Channel).where(Channel.version_id == version_id).order_by(Channel.channel_index)
    ).all()
    return [ChannelResponse.model_validate(channel) for channel in channels]


@router.get("/recordings/{recording_id}/events", response_model=list[EventResponse])
def list_events(
    recording_id: str,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
) -> list[EventResponse]:
    recording = _public_or_authorized_recording(session, recording_id, identity)
    events = session.scalars(
        select(Event)
        .where(Event.version_id == _active_version(recording))
        .order_by(Event.onset_seconds, Event.id)
    ).all()
    return [EventResponse.model_validate(event) for event in events]


@router.get("/recordings/{recording_id}/waveform", response_model=None)
def get_waveform(
    recording_id: str,
    request: Request,
    response: Response,
    start_seconds: float = Query(0, ge=0),
    duration_seconds: float = Query(10, gt=0),
    channel_index: list[int] = Query(default=[]),
    pixel_width: int = Query(1200, ge=64),
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> dict[str, Any] | Response:
    """Return one bounded viewport window.

    A RecordingVersion is immutable once it exists (ADR 0002/0007), so this
    response is fully determined by (version_id, channels, window,
    resolution) -- a strong ETag and a cache-aside Redis lookup are both
    safe here with no invalidation logic needed, only a TTL. `If-None-Match`
    short-circuits before touching Redis or the signal store at all.
    """

    recording = _public_or_authorized_recording(session, recording_id, identity)
    if len(channel_index) > runtime.settings.max_waveform_channels:
        raise ValidationError(
            "too_many_channels",
            f"Choose at most {runtime.settings.max_waveform_channels} channels per view.",
        )
    if pixel_width > runtime.settings.max_waveform_pixels:
        raise ValidationError(
            "viewport_too_wide",
            f"pixel_width may not exceed {runtime.settings.max_waveform_pixels}.",
        )
    version_id = _active_version(recording)
    if not channel_index:
        channel_count = int(recording.safe_metadata.get("channel_count", 0))
        channel_index = list(range(min(channel_count, 32)))

    cache_key = runtime.cache.waveform_key(
        version_id, channel_index, start_seconds, duration_seconds, pixel_width
    )
    etag = f'"{cache_key.rsplit(":", 1)[-1]}"'
    cache_headers = {"ETag": etag, "Cache-Control": "private, max-age=3600, immutable"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)

    cached = runtime.cache.get_waveform(cache_key)
    if cached is not None:
        response.headers.update(cache_headers)
        return cached

    window = runtime.signal_store.query(
        version_id, channel_index, start_seconds, duration_seconds, pixel_width
    )
    point_count = len(window.bin_start_seconds) * len(window.channels) * 2
    if point_count > runtime.settings.max_waveform_points:
        raise ValidationError(
            "waveform_too_large", "Reduce visible channels or viewport width and try again."
        )
    window_dict = asdict(window)
    runtime.cache.set_waveform(cache_key, window_dict)
    response.headers.update(cache_headers)
    return window_dict


@router.get("/recordings/{recording_id}/annotations", response_model=AnnotationPage)
def list_annotations(
    recording_id: str,
    start_seconds: float | None = Query(default=None, ge=0),
    end_seconds: float | None = Query(default=None, gt=0),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
) -> AnnotationPage:
    """List annotations, oldest-onset first, cursor-paginated.

    `start_seconds`/`end_seconds` bound the query to annotations whose own
    onset falls in that window -- the viewer uses this to avoid loading
    every annotation in a multi-hour recording just to render one viewport.
    This does not also match an annotation that started before the window
    but is still running through it (there is no index on duration to make
    that cheap); the viewer widens its query window slightly to compensate.
    """

    _public_or_authorized_recording(session, recording_id, identity)
    stmt = select(Annotation).where(
        Annotation.recording_id == recording_id, Annotation.deleted_at.is_(None)
    )
    if start_seconds is not None:
        stmt = stmt.where(Annotation.onset_seconds >= start_seconds)
    if end_seconds is not None:
        stmt = stmt.where(Annotation.onset_seconds < end_seconds)
    stmt = stmt.order_by(Annotation.onset_seconds, Annotation.id)
    rows, has_more = paginate(
        session,
        stmt,
        sort_column=Annotation.onset_seconds,
        id_column=Annotation.id,
        cursor=cursor,
        limit=limit,
        descending=False,
    )
    next_cursor = (
        encode_float_cursor(rows[-1].onset_seconds, rows[-1].id) if has_more and rows else None
    )
    return AnnotationPage(
        items=[AnnotationResponse.model_validate(row) for row in rows], next_cursor=next_cursor
    )


@router.post(
    "/recordings/{recording_id}/annotations", response_model=AnnotationResponse, status_code=201
)
def create_annotation(
    recording_id: str,
    body: AnnotationCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> AnnotationResponse:
    recording = recording_for_user(session, user.id, recording_id, Capability.ANNOTATE)
    if (
        body.onset_seconds + body.duration_seconds
        > float(recording.safe_metadata.get("duration_seconds", 0)) + 1e-9
    ):
        raise ValidationError("annotation_out_of_bounds", "The annotation exceeds the recording.")
    annotation = Annotation(
        recording_id=recording.id,
        version_id=recording.active_version_id,
        creator_id=user.id,
        updater_id=user.id,
        onset_seconds=body.onset_seconds,
        duration_seconds=body.duration_seconds,
        label=body.label.strip(),
        description=body.description.strip(),
    )
    session.add(annotation)
    track_product_event(
        session,
        "annotation_created",
        actor_id=user.id,
        workspace_id=recording.workspace_id,
        recording_id=recording.id,
    )
    session.commit()
    return AnnotationResponse.model_validate(annotation)


@router.patch("/annotations/{annotation_id}", response_model=AnnotationResponse)
def update_annotation(
    annotation_id: str,
    body: AnnotationUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> AnnotationResponse:
    annotation = session.get(Annotation, annotation_id)
    if annotation is None or annotation.deleted_at is not None:
        raise ForbiddenError()
    recording = recording_for_user(session, user.id, annotation.recording_id, Capability.ANNOTATE)
    if (
        body.onset_seconds + body.duration_seconds
        > float(recording.safe_metadata.get("duration_seconds", 0)) + 1e-9
    ):
        raise ValidationError("annotation_out_of_bounds", "The annotation exceeds the recording.")
    # Compare-and-swap in the UPDATE's WHERE clause, not in Python: two
    # concurrent PATCHes that both read revision=N can otherwise both pass a
    # Python-side equality check and one silently overwrites the other. Only
    # one UPDATE can match this row per revision value, so the loser gets
    # rowcount 0 -- an explicit 409 -- instead of being silently discarded.
    result = session.execute(
        sa_update(Annotation)
        .where(
            Annotation.id == annotation_id,
            Annotation.revision == body.revision,
            Annotation.deleted_at.is_(None),
        )
        .values(
            onset_seconds=body.onset_seconds,
            duration_seconds=body.duration_seconds,
            label=body.label.strip(),
            description=body.description.strip(),
            updater_id=user.id,
            revision=Annotation.revision + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        session.rollback()
        raise ConflictError(
            "annotation_revision_conflict", "This annotation changed. Reload before editing it."
        )
    track_product_event(
        session,
        "annotation_updated",
        actor_id=user.id,
        workspace_id=recording.workspace_id,
        recording_id=recording.id,
    )
    session.commit()
    session.refresh(annotation)
    return AnnotationResponse.model_validate(annotation)


@router.delete("/annotations/{annotation_id}", status_code=204)
def delete_annotation(
    annotation_id: str,
    body: AnnotationDelete,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> None:
    from datetime import UTC, datetime

    annotation = session.get(Annotation, annotation_id)
    if annotation is None or annotation.deleted_at is not None:
        raise ForbiddenError()
    recording = recording_for_user(session, user.id, annotation.recording_id, Capability.ANNOTATE)
    result = session.execute(
        sa_update(Annotation)
        .where(
            Annotation.id == annotation_id,
            Annotation.revision == body.revision,
            Annotation.deleted_at.is_(None),
        )
        .values(
            deleted_at=datetime.now(UTC),
            updater_id=user.id,
            revision=Annotation.revision + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        session.rollback()
        raise ConflictError(
            "annotation_revision_conflict", "This annotation changed. Reload before deleting it."
        )
    track_product_event(
        session,
        "annotation_deleted",
        actor_id=user.id,
        workspace_id=recording.workspace_id,
        recording_id=recording.id,
    )
    session.commit()


@router.get("/recordings/{recording_id}/bad-channels", response_model=list[BadChannelResponse])
def list_bad_channels(
    recording_id: str,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
) -> list[BadChannelResponse]:
    recording = _public_or_authorized_recording(session, recording_id, identity)
    rows = session.scalars(
        select(BadChannel).where(
            BadChannel.recording_id == recording.id,
            BadChannel.version_id == recording.active_version_id,
        )
    ).all()
    return [BadChannelResponse.model_validate(row) for row in rows]


@router.put(
    "/recordings/{recording_id}/bad-channels/{channel_id}", response_model=BadChannelResponse
)
def set_bad_channel(
    recording_id: str,
    channel_id: str,
    body: BadChannelUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> BadChannelResponse:
    """Compare-and-swap review state for one channel.

    Mirrors update_annotation's CAS: the client sends the revision it last
    observed (0 for "no one has reviewed this channel yet"), and a
    conditional UPDATE -- not a Python-side equality check -- decides the
    winner when two reviewers race. The insert path (revision 0, row does
    not exist yet) has the same race one level up: two concurrent first
    reviews can both pass the "row is None" check, so the unique constraint
    on (recording_id, version_id, channel_id) is the real backstop there,
    surfaced as the same 409 rather than a raw IntegrityError.
    """

    recording = recording_for_user(session, user.id, recording_id, Capability.ANNOTATE)
    channel = session.scalar(
        select(Channel).where(
            Channel.id == channel_id, Channel.version_id == recording.active_version_id
        )
    )
    if channel is None:
        raise ForbiddenError()
    row = session.scalar(
        select(BadChannel).where(
            BadChannel.recording_id == recording.id,
            BadChannel.version_id == recording.active_version_id,
            BadChannel.channel_id == channel.id,
        )
    )
    current_revision = row.revision if row is not None else 0
    if body.revision != current_revision:
        raise ConflictError(
            "bad_channel_revision_conflict",
            "This channel's review status changed. Reload before editing it.",
        )
    if row is None:
        row = BadChannel(
            recording_id=recording.id,
            version_id=recording.active_version_id,
            channel_id=channel.id,
            actor_id=user.id,
            state=body.state,
            reason=body.reason.strip(),
            revision=1,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise ConflictError(
                "bad_channel_revision_conflict",
                "This channel's review status changed. Reload before editing it.",
            ) from None
    else:
        result = session.execute(
            sa_update(BadChannel)
            .where(BadChannel.id == row.id, BadChannel.revision == body.revision)
            .values(
                state=body.state,
                reason=body.reason.strip(),
                actor_id=user.id,
                revision=BadChannel.revision + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            session.rollback()
            raise ConflictError(
                "bad_channel_revision_conflict",
                "This channel's review status changed. Reload before editing it.",
            )
        session.refresh(row)
    if body.state == "confirmed_bad":
        track_product_event(
            session,
            "bad_channel_marked",
            actor_id=user.id,
            workspace_id=recording.workspace_id,
            recording_id=recording.id,
        )
    session.commit()
    session.refresh(row)
    return BadChannelResponse.model_validate(row)


@router.get("/recordings/{recording_id}/qc")
def get_qc(
    recording_id: str,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> dict[str, Any]:
    """QC summary, cache-aside keyed by the immutable active version_id.

    Same reasoning as the waveform cache (ADR 0002/0007): the metrics and
    flags for a version_id cannot change once QC has run, so there is no
    invalidation to implement -- only a TTL.
    """

    recording = _public_or_authorized_recording(session, recording_id, identity)
    version_id = _active_version(recording)
    cached = runtime.cache.get_qc(version_id)
    if cached is not None:
        return cached
    metrics = session.execute(
        select(QCMetric, Channel.name)
        .outerjoin(Channel, Channel.id == QCMetric.channel_id)
        .where(QCMetric.version_id == version_id)
        .order_by(Channel.channel_index, QCMetric.rule_id)
    ).all()
    flags = session.execute(
        select(QCFlag, QCMetric.channel_id)
        .join(QCMetric, QCMetric.id == QCFlag.metric_id)
        .where(QCMetric.version_id == version_id)
    ).all()
    result = {
        "disclaimer": "Automated review heuristics, not medical or diagnostic conclusions.",
        "metrics": [
            {
                "id": metric.id,
                "channel_id": metric.channel_id,
                "channel_name": channel_name,
                "rule_id": metric.rule_id,
                "rule_version": metric.rule_version,
                "value": metric.value,
                "unit": metric.unit,
                "parameters": metric.parameters,
                "coverage": metric.coverage,
            }
            for metric, channel_name in metrics
        ],
        "flags": [
            {
                "id": flag.id,
                "metric_id": flag.metric_id,
                "channel_id": channel_id,
                "severity": flag.severity,
                "message": flag.safe_message,
                "state": flag.state,
            }
            for flag, channel_id in flags
        ],
    }
    runtime.cache.set_qc(version_id, result)
    return result


@router.post(
    "/recordings/{recording_id}/processing-runs",
    response_model=ProcessingRunResponse,
    status_code=202,
)
def create_processing_run(
    recording_id: str,
    body: ProcessingRunCreate,
    background: BackgroundTasks,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> ProcessingRunResponse:
    """Start a processing run.

    A client-supplied Idempotency-Key makes retries safe: submitting the same
    key twice (a network retry, a double-click) returns the original run
    instead of launching duplicate processing work and a duplicate
    RecordingVersion. Without a key, each call is a genuinely new request, so
    no dedup is attempted -- the client is asserting the retry is a distinct
    intent.
    """

    recording = recording_for_user(session, user.id, recording_id, Capability.PROCESS)
    version_id = _active_version(recording)
    channel_count = int(recording.safe_metadata.get("channel_count", 0))
    indices = body.parameters["channel_indices"]
    if max(indices) >= channel_count:
        raise ValidationError("invalid_channels", "A reference channel index is not available.")

    key = (
        f"process:{recording.id}:{idempotency_key}"
        if idempotency_key
        else f"process:{recording.id}:{uuid.uuid4().hex}"
    )

    def _existing_run() -> ProcessingRun | None:
        existing_job = session.scalar(
            select(Job).where(
                Job.workspace_id == recording.workspace_id, Job.idempotency_key == key
            )
        )
        if existing_job is None:
            return None
        return session.scalar(select(ProcessingRun).where(ProcessingRun.job_id == existing_job.id))

    if idempotency_key:
        found = _existing_run()
        if found is not None:
            response.status_code = 200
            return ProcessingRunResponse.model_validate(found)

    job = Job(
        workspace_id=recording.workspace_id,
        kind=JobKind.PROCESSING,
        subject_type="recording",
        subject_id=recording.id,
        idempotency_key=key,
        stage="Queued",
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        # Lost a race against a concurrent request carrying the same
        # Idempotency-Key; the winner's row is now visible.
        session.rollback()
        found = _existing_run()
        if found is None:
            raise
        response.status_code = 200
        return ProcessingRunResponse.model_validate(found)

    run = ProcessingRun(
        recording_id=recording.id,
        input_version_id=version_id,
        creator_id=user.id,
        job_id=job.id,
        operation_id=body.operation_id,
        operation_version="1",
        parameters=body.parameters,
    )
    session.add(run)
    track_product_event(
        session,
        "processing_run_created",
        actor_id=user.id,
        workspace_id=recording.workspace_id,
        recording_id=recording.id,
        properties={"operation_id": body.operation_id},
    )
    session.commit()
    _dispatch_processing(background, runtime, session, job.id)
    response.status_code = 202
    return ProcessingRunResponse.model_validate(run)


@router.get(
    "/recordings/{recording_id}/processing-runs", response_model=list[ProcessingRunResponse]
)
def list_processing_runs(
    recording_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> list[ProcessingRunResponse]:
    recording_for_user(session, user.id, recording_id)
    runs = session.scalars(
        select(ProcessingRun)
        .where(ProcessingRun.recording_id == recording_id)
        .order_by(ProcessingRun.created_at.desc())
    ).all()
    return [ProcessingRunResponse.model_validate(run) for run in runs]


@router.post("/recordings/{recording_id}/exports", response_model=ExportResponse, status_code=201)
def create_export(
    recording_id: str,
    body: ExportCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> ExportResponse:
    recording = recording_for_user(session, user.id, recording_id, Capability.EXPORT)
    version_id = _active_version(recording)
    export = Export(
        recording_id=recording.id,
        version_id=version_id,
        creator_id=user.id,
        format_id=body.format_id,
        status="RUNNING",
        loss_notes=["This metadata export does not contain raw signal samples."],
    )
    session.add(export)
    session.flush()
    track_product_event(
        session,
        "export_started",
        actor_id=user.id,
        workspace_id=recording.workspace_id,
        recording_id=recording.id,
        properties={"format": body.format_id},
    )
    session.commit()
    suffix = "csv" if body.format_id == "annotations_csv" else "json"
    key = f"exports/{recording.id}/{export.id}.{suffix}"
    try:
        with tempfile.TemporaryDirectory(prefix="nw-export-") as temp_name:
            path = Path(temp_name) / f"export.{suffix}"
            if body.format_id == "annotations_csv":
                annotations = session.scalars(
                    select(Annotation).where(
                        Annotation.recording_id == recording.id,
                        Annotation.deleted_at.is_(None),
                    )
                ).all()
                export_annotations_csv(
                    (
                        {
                            "id": item.id,
                            "onset_seconds": item.onset_seconds,
                            "duration_seconds": item.duration_seconds,
                            "label": item.label,
                            "description": item.description,
                            "creator_id": item.creator_id,
                            "revision": item.revision,
                        }
                        for item in annotations
                    ),
                    path,
                )
            elif body.format_id == "provenance_json":
                versions = session.scalars(
                    select(RecordingVersion).where(RecordingVersion.recording_id == recording.id)
                ).all()
                runs = session.scalars(
                    select(ProcessingRun).where(ProcessingRun.recording_id == recording.id)
                ).all()
                export_provenance_json(
                    recording={
                        "id": recording.id,
                        "name": recording.name,
                        "format_id": recording.format_id,
                        "source_provenance": recording.source_provenance,
                    },
                    versions=[
                        {
                            "id": version.id,
                            "parent_version_id": version.parent_version_id,
                            "kind": version.kind,
                            "manifest_sha256": version.manifest_sha256,
                            "sampling_frequency_hz": version.sampling_frequency_hz,
                            "sample_count": version.sample_count,
                            "software": version.software,
                        }
                        for version in versions
                    ],
                    processing_runs=[
                        {
                            "id": run.id,
                            "input_version_id": run.input_version_id,
                            "output_version_id": run.output_version_id,
                            "operation_id": run.operation_id,
                            "operation_version": run.operation_version,
                            "parameters": run.parameters,
                        }
                        for run in runs
                    ],
                    destination=path,
                )
            else:
                qc = get_qc(
                    recording.id,
                    Identity(user.external_subject, user.email, user.display_name),
                    session,
                    runtime,
                )
                path.write_text(json.dumps(qc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            info = runtime.object_store.put_file(key, path)
    except BaseException as exc:
        session.rollback()
        failed_export = session.get(Export, export.id)
        if failed_export is not None:
            failed_export.status = "FAILED"
        track_product_event(
            session,
            "export_failed",
            actor_id=user.id,
            workspace_id=recording.workspace_id,
            recording_id=recording.id,
            properties={"format": body.format_id, "failure_class": type(exc).__name__},
        )
        session.commit()
        raise
    export.status = "READY"
    export.object_key = key
    export.size_bytes = info.size_bytes
    export.sha256 = info.sha256
    track_product_event(
        session,
        "export_completed",
        actor_id=user.id,
        workspace_id=recording.workspace_id,
        recording_id=recording.id,
        properties={"format": body.format_id},
    )
    session.commit()
    return ExportResponse.model_validate(export)


@router.get("/exports/{export_id}/content")
def download_export(
    export_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> StreamingResponse:
    export = session.get(Export, export_id)
    if export is None or export.object_key is None:
        raise ForbiddenError()
    recording_for_user(session, user.id, export.recording_id, Capability.EXPORT)
    data = runtime.object_store.read_bytes(export.object_key)
    media = "text/csv" if export.format_id == "annotations_csv" else "application/json"
    suffix = "csv" if export.format_id == "annotations_csv" else "json"
    headers = {"Content-Disposition": f'attachment; filename="neuroworkspace-export.{suffix}"'}
    return StreamingResponse(iter([data]), media_type=media, headers=headers)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    response: Response,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> JobResponse:
    job = job_for_user(session, user.id, job_id)
    if job.status.value in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
        response.headers["Retry-After"] = "2"
    return JobResponse.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> JobResponse:
    """Request cancellation.

    A QUEUED job is cancelled immediately. A RUNNING job cannot be
    interrupted safely from here (its worker may be inside a blocking C
    extension call), so this only flags CANCEL_REQUESTED; the executor
    finalizes it to CANCELLED at its next cooperative checkpoint -- see
    is_cancel_requested() in services.jobs. A job already in a terminal
    state is left untouched (cancelling a SUCCEEDED job is meaningless, not
    an error the client needs to see).
    """

    job_for_user(session, user.id, job_id, Capability.PROCESS)
    job = request_cancellation(session, job_id)
    if job is None:
        raise ForbiddenError()
    return JobResponse.model_validate(job)


@router.delete("/recordings/{recording_id}", response_model=JobResponse, status_code=202)
def delete_recording(
    recording_id: str,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> JobResponse:
    recording = recording_for_user(session, user.id, recording_id, Capability.DELETE_RECORDING)
    if recording.is_demo:
        raise ValidationError("demo_protected", "The shared sample recording cannot be deleted.")
    recording.state = RecordingState.DELETING
    from datetime import UTC, datetime

    recording.deleted_at = datetime.now(UTC)
    job = Job(
        workspace_id=recording.workspace_id,
        kind=JobKind.DELETION,
        subject_type="recording",
        subject_id=recording.id,
        idempotency_key=f"delete:{recording.id}",
        stage="Deleting",
    )
    session.add(job)
    session.commit()
    _dispatch_deletion(background, runtime, session, job.id)
    return JobResponse.model_validate(job)
