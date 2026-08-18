"""Workspace-scoped direct upload initiation and asynchronous completion."""

from __future__ import annotations

import unicodedata
import uuid
from pathlib import Path

from celery import Celery
from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from neuro_api.runtime import Runtime, current_user, get_db, get_runtime
from neuro_api.schemas import (
    UploadAssetResponse,
    UploadCompleteResponse,
    UploadCreate,
    UploadResponse,
)
from neuro_workspace.adapters.registry import default_registry
from neuro_workspace.db.models import Job, Recording, SourceAsset, Upload, User
from neuro_workspace.domain import Capability, JobKind, RecordingState
from neuro_workspace.errors import ConflictError, ForbiddenError, ValidationError
from neuro_workspace.product_analytics import format_category, size_bucket, track_product_event
from neuro_workspace.services.access import project_for_user
from neuro_workspace.services.jobs import execute_ingestion_job

router = APIRouter()


def _safe_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).strip()
    if not normalized or Path(normalized).name != normalized or normalized in {".", ".."}:
        raise ValidationError("unsafe_filename", "Use a filename without folders or path segments.")
    if any(ord(character) < 32 for character in normalized):
        raise ValidationError("unsafe_filename", "The filename contains control characters.")
    return normalized


def _dispatch_ingestion(background: BackgroundTasks, runtime: Runtime, job_id: str) -> None:
    if runtime.settings.worker_eager:
        background.add_task(
            execute_ingestion_job,
            factory=runtime.session_factory,
            object_store=runtime.object_store,
            signal_store=runtime.signal_store,
            registry=default_registry,
            settings=runtime.settings,
            job_id=job_id,
        )
    else:
        Celery(broker=runtime.settings.redis_url).send_task(
            "neuroworkspace.ingest", args=[job_id], queue="ingestion"
        )


@router.post("/projects/{project_id}/uploads", response_model=UploadResponse, status_code=201)
def create_upload(
    project_id: str,
    body: UploadCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> UploadResponse:
    project = project_for_user(session, user.id, project_id, Capability.CREATE)
    total = sum(asset.size_bytes for asset in body.assets)
    try:
        if len(body.assets) > runtime.settings.max_upload_files:
            raise ValidationError("too_many_files", "The upload contains too many files.")
        if total > runtime.settings.max_upload_bytes:
            raise ValidationError("too_large", "The upload exceeds the workspace size limit.")
        if body.format_hint == "csv" and body.csv_config is None:
            raise ValidationError(
                "csv_config_required", "Configure the delimited text import first."
            )
        names = [_safe_filename(asset.name) for asset in body.assets]
        if len({name.casefold() for name in names}) != len(names):
            raise ValidationError("duplicate_filename", "Upload filenames must be unique.")
    except ValidationError as exc:
        track_product_event(
            session,
            "upload_failed",
            actor_id=user.id,
            workspace_id=project.workspace_id,
            properties={
                "format": format_category(body.format_hint),
                "size_bucket": size_bucket(total),
                "failure_class": exc.code,
            },
        )
        session.commit()
        raise

    upload = Upload(
        workspace_id=project.workspace_id,
        project_id=project.id,
        creator_id=user.id,
        status="UPLOADING",
        format_hint=body.format_hint,
        csv_config=body.csv_config.model_dump(mode="json") if body.csv_config else None,
        total_bytes=total,
    )
    # Recording name is retained in a safe non-scientific field until completion.
    upload.csv_config = {
        **(upload.csv_config or {}),
        "_recording_name": body.recording_name.strip(),
    }
    session.add(upload)
    session.flush()
    track_product_event(
        session,
        "upload_started",
        actor_id=user.id,
        workspace_id=project.workspace_id,
        properties={
            "format": format_category(body.format_hint),
            "size_bucket": size_bucket(total),
        },
    )
    responses: list[UploadAssetResponse] = []
    for request_asset, safe_name in zip(body.assets, names, strict=True):
        asset_id = str(uuid.uuid4())
        key = f"uploads/{upload.id}/{asset_id}/{safe_name}"
        asset = SourceAsset(
            id=asset_id,
            upload_id=upload.id,
            object_key=key,
            safe_name=safe_name,
            role=request_asset.role,
            size_bytes=request_asset.size_bytes,
            media_type=request_asset.media_type,
        )
        session.add(asset)
        signed = runtime.object_store.presign_put(key)
        responses.append(
            UploadAssetResponse(
                id=asset.id,
                safe_name=safe_name,
                role=asset.role,
                size_bytes=asset.size_bytes,
                upload_url=signed
                or (
                    f"{runtime.settings.api_public_url}/api/uploads/{upload.id}"
                    f"/assets/{asset.id}/content"
                ),
                direct=signed is not None,
            )
        )
    session.commit()
    return UploadResponse(
        id=upload.id,
        status=upload.status,
        recording_name=body.recording_name.strip(),
        assets=responses,
    )


@router.put("/uploads/{upload_id}/assets/{asset_id}/content", status_code=204)
def upload_local_content(
    upload_id: str,
    asset_id: str,
    file: UploadFile,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> None:
    if runtime.settings.object_store_backend != "local":
        raise ConflictError(
            "direct_upload_required",
            "Upload this asset with the signed object-storage URL returned at initiation.",
        )
    upload = session.scalar(
        select(Upload).where(Upload.id == upload_id, Upload.creator_id == user.id)
    )
    asset = session.scalar(
        select(SourceAsset).where(SourceAsset.id == asset_id, SourceAsset.upload_id == upload_id)
    )
    if upload is None or asset is None:
        raise ForbiddenError()
    if upload.status != "UPLOADING":
        raise ConflictError("upload_not_open", "This upload is no longer accepting files.")
    info = runtime.object_store.put_stream(
        asset.object_key, file.file, runtime.settings.max_upload_bytes
    )
    if info.size_bytes != asset.size_bytes:
        runtime.object_store.delete_prefix(asset.object_key)
        track_product_event(
            session,
            "upload_failed",
            actor_id=user.id,
            workspace_id=upload.workspace_id,
            properties={
                "format": format_category(upload.format_hint),
                "size_bucket": size_bucket(upload.total_bytes),
                "failure_class": "size_mismatch",
            },
        )
        session.commit()
        raise ValidationError(
            "size_mismatch",
            f"The uploaded size for {asset.safe_name} does not match its declaration.",
        )
    asset.sha256 = info.sha256
    session.commit()


@router.post("/uploads/{upload_id}/complete", response_model=UploadCompleteResponse)
def complete_upload(
    upload_id: str,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> UploadCompleteResponse:
    upload = session.scalar(
        select(Upload).where(Upload.id == upload_id, Upload.creator_id == user.id)
    )
    if upload is None:
        raise ForbiddenError()
    if upload.status != "UPLOADING":
        raise ConflictError("upload_not_open", "This upload has already been completed or aborted.")
    assets = list(session.scalars(select(SourceAsset).where(SourceAsset.upload_id == upload.id)))
    if runtime.settings.object_store_backend == "local" and any(
        asset.sha256 is None for asset in assets
    ):
        track_product_event(
            session,
            "upload_failed",
            actor_id=user.id,
            workspace_id=upload.workspace_id,
            properties={
                "format": format_category(upload.format_hint),
                "size_bucket": size_bucket(upload.total_bytes),
                "failure_class": "upload_incomplete",
            },
        )
        session.commit()
        raise ValidationError(
            "upload_incomplete", "Upload every file before completing the upload."
        )
    config = dict(upload.csv_config or {})
    recording_name = str(config.pop("_recording_name", "Recording"))
    upload.csv_config = config or None
    recording = Recording(
        workspace_id=upload.workspace_id,
        project_id=upload.project_id,
        creator_id=user.id,
        name=recording_name,
        format_id=upload.format_hint,
        state=RecordingState.QUEUED,
    )
    session.add(recording)
    session.flush()
    for asset in assets:
        asset.recording_id = recording.id
    job = Job(
        workspace_id=recording.workspace_id,
        kind=JobKind.INGESTION,
        subject_type="recording",
        subject_id=recording.id,
        idempotency_key=f"ingest:{recording.id}:{uuid.uuid4().hex}",
        stage="Inspecting",
    )
    session.add(job)
    upload.status = "COMPLETED"
    track_product_event(
        session,
        "upload_completed",
        actor_id=user.id,
        workspace_id=recording.workspace_id,
        recording_id=recording.id,
        properties={
            "format": format_category(upload.format_hint),
            "size_bucket": size_bucket(upload.total_bytes),
        },
    )
    session.commit()
    _dispatch_ingestion(background, runtime, job.id)
    return UploadCompleteResponse(
        recording_id=recording.id, job_id=job.id, state=recording.state.value
    )
