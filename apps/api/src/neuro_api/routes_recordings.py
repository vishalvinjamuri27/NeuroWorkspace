"""Recording, viewer, annotation, QC, processing, export, and deletion routes."""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from celery import Celery
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
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
from neuro_workspace.services.access import (
    ensure_user,
    job_for_user,
    project_for_user,
    recording_for_user,
)
from neuro_workspace.services.jobs import execute_deletion_job, execute_processing_job

router = APIRouter()


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


def _dispatch_processing(background: BackgroundTasks, runtime: Runtime, job_id: str) -> None:
    if runtime.settings.worker_eager:
        background.add_task(
            execute_processing_job,
            factory=runtime.session_factory,
            signal_store=runtime.signal_store,
            settings=runtime.settings,
            job_id=job_id,
        )
    else:
        Celery(broker=runtime.settings.redis_url).send_task("neuroworkspace.process", args=[job_id])


def _dispatch_deletion(background: BackgroundTasks, runtime: Runtime, job_id: str) -> None:
    if runtime.settings.worker_eager:
        background.add_task(
            execute_deletion_job,
            factory=runtime.session_factory,
            object_store=runtime.object_store,
            signal_store=runtime.signal_store,
            job_id=job_id,
        )
    else:
        Celery(broker=runtime.settings.redis_url).send_task("neuroworkspace.delete", args=[job_id])


@router.get("/projects/{project_id}/recordings", response_model=list[RecordingResponse])
def list_recordings(
    project_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> list[RecordingResponse]:
    project = project_for_user(session, user.id, project_id)
    rows = session.scalars(
        select(Recording)
        .where(Recording.project_id == project.id, Recording.deleted_at.is_(None))
        .order_by(Recording.created_at.desc(), Recording.id)
    ).all()
    return [RecordingResponse.model_validate(row) for row in rows]


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


@router.get("/recordings/{recording_id}/waveform")
def get_waveform(
    recording_id: str,
    start_seconds: float = Query(0, ge=0),
    duration_seconds: float = Query(10, gt=0),
    channel_index: list[int] = Query(default=[]),
    pixel_width: int = Query(1200, ge=64),
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> dict[str, Any]:
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
    window = runtime.signal_store.query(
        version_id, channel_index, start_seconds, duration_seconds, pixel_width
    )
    point_count = len(window.bin_start_seconds) * len(window.channels) * 2
    if point_count > runtime.settings.max_waveform_points:
        raise ValidationError(
            "waveform_too_large", "Reduce visible channels or viewport width and try again."
        )
    return asdict(window)


@router.get("/recordings/{recording_id}/annotations", response_model=list[AnnotationResponse])
def list_annotations(
    recording_id: str,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
) -> list[AnnotationResponse]:
    _public_or_authorized_recording(session, recording_id, identity)
    annotations = session.scalars(
        select(Annotation)
        .where(Annotation.recording_id == recording_id, Annotation.deleted_at.is_(None))
        .order_by(Annotation.onset_seconds, Annotation.id)
    ).all()
    return [AnnotationResponse.model_validate(annotation) for annotation in annotations]


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
    if annotation.revision != body.revision:
        raise ConflictError(
            "annotation_revision_conflict", "This annotation changed. Reload before editing it."
        )
    if (
        body.onset_seconds + body.duration_seconds
        > float(recording.safe_metadata.get("duration_seconds", 0)) + 1e-9
    ):
        raise ValidationError("annotation_out_of_bounds", "The annotation exceeds the recording.")
    annotation.onset_seconds = body.onset_seconds
    annotation.duration_seconds = body.duration_seconds
    annotation.label = body.label.strip()
    annotation.description = body.description.strip()
    annotation.updater_id = user.id
    annotation.revision += 1
    session.commit()
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
    recording_for_user(session, user.id, annotation.recording_id, Capability.ANNOTATE)
    if annotation.revision != body.revision:
        raise ConflictError(
            "annotation_revision_conflict", "This annotation changed. Reload before deleting it."
        )
    annotation.deleted_at = datetime.now(UTC)
    annotation.updater_id = user.id
    annotation.revision += 1
    session.commit()


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
    if row is None:
        row = BadChannel(
            recording_id=recording.id,
            version_id=recording.active_version_id,
            channel_id=channel.id,
            actor_id=user.id,
        )
        session.add(row)
    else:
        row.revision += 1
    row.state = body.state
    row.reason = body.reason.strip()
    row.actor_id = user.id
    session.commit()
    return BadChannelResponse.model_validate(row)


@router.get("/recordings/{recording_id}/qc")
def get_qc(
    recording_id: str,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    recording = _public_or_authorized_recording(session, recording_id, identity)
    version_id = _active_version(recording)
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
    return {
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


@router.post(
    "/recordings/{recording_id}/processing-runs",
    response_model=ProcessingRunResponse,
    status_code=202,
)
def create_processing_run(
    recording_id: str,
    body: ProcessingRunCreate,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> ProcessingRunResponse:
    recording = recording_for_user(session, user.id, recording_id, Capability.PROCESS)
    version_id = _active_version(recording)
    channel_count = int(recording.safe_metadata.get("channel_count", 0))
    indices = body.parameters["channel_indices"]
    if max(indices) >= channel_count:
        raise ValidationError("invalid_channels", "A reference channel index is not available.")
    job = Job(
        workspace_id=recording.workspace_id,
        kind=JobKind.PROCESSING,
        subject_type="recording",
        subject_id=recording.id,
        idempotency_key=f"process:{recording.id}:{uuid.uuid4().hex}",
        stage="Queued",
    )
    session.add(job)
    session.flush()
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
    session.commit()
    _dispatch_processing(background, runtime, job.id)
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
    suffix = "csv" if body.format_id == "annotations_csv" else "json"
    key = f"exports/{recording.id}/{export.id}.{suffix}"
    with tempfile.TemporaryDirectory(prefix="nw-export-") as temp_name:
        path = Path(temp_name) / f"export.{suffix}"
        if body.format_id == "annotations_csv":
            annotations = session.scalars(
                select(Annotation).where(
                    Annotation.recording_id == recording.id, Annotation.deleted_at.is_(None)
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
            )
            path.write_text(json.dumps(qc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        info = runtime.object_store.put_file(key, path)
    export.status = "READY"
    export.object_key = key
    export.size_bytes = info.size_bytes
    export.sha256 = info.sha256
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
    if job.status.value in {"QUEUED", "RUNNING"}:
        response.headers["Retry-After"] = "2"
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
    _dispatch_deletion(background, runtime, job.id)
    return JobResponse.model_validate(job)
