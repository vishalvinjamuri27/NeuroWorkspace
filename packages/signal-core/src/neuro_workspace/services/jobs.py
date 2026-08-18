"""Idempotent job execution shared by eager tests and Celery workers."""

from __future__ import annotations

import hashlib
import hmac
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
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
from neuro_workspace.processing import average_reference
from neuro_workspace.signal_store import LocalZarrSignalStore
from neuro_workspace.storage import ObjectStore


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


def _begin_job(session: Session, job_id: str, recording_id: str) -> tuple[Job, Recording]:
    job = session.get(Job, job_id)
    recording = session.get(Recording, recording_id)
    if job is None or recording is None:
        raise RuntimeError("job subject is missing")
    if job.status == JobState.SUCCEEDED:
        return job, recording
    if recording.state in {RecordingState.DELETING, RecordingState.DELETED}:
        job.status = JobState.CANCELLED
        job.stage = "Cancelled"
        session.commit()
        return job, recording
    job.status = JobState.RUNNING
    job.attempt += 1
    job.progress = 0.03
    job.stage = "Inspecting"
    job.heartbeat_at = datetime.now(UTC)
    recording.state = RecordingState.INGESTING
    session.commit()
    return job, recording


def execute_ingestion_job(
    *,
    factory: sessionmaker[Session],
    object_store: ObjectStore,
    signal_store: LocalZarrSignalStore,
    registry: AdapterRegistry,
    settings: Settings,
    job_id: str,
) -> None:
    """Execute one ingestion job; re-entry after success is a no-op."""

    session = factory()
    recording_id = ""
    version_id = str(uuid.uuid4())
    try:
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError("job is missing")
        recording_id = job.subject_id
        job, recording = _begin_job(session, job_id, recording_id)
        if job.status in {JobState.SUCCEEDED, JobState.CANCELLED}:
            return
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
            config = CSVImportConfig(**upload.csv_config) if upload.csv_config else None
            job.progress = 0.12
            job.stage = "Processing"
            recording.state = RecordingState.PROCESSING
            session.commit()
            result = ingest_recording(
                adapter=adapter,
                assets=paths,
                import_config=config,
                version_id=version_id,
                signal_store=signal_store,
                source_hashes=tuple(source_hashes),
                build_version=settings.build_version,
            )

        # A deletion request that arrived during parsing must win before publication.
        session.refresh(recording)
        if recording.state in {RecordingState.DELETING, RecordingState.DELETED}:
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
            manifest_sha256=_sha256_file(signal_store.version_path(version_id) / "manifest.json"),
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
        session.commit()
    except BaseException as exc:
        session.rollback()
        if version_id:
            signal_store.delete(version_id)
        code, message = _safe_error(exc)
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
        session.commit()
        raise
    finally:
        session.close()


def execute_processing_job(
    *,
    factory: sessionmaker[Session],
    signal_store: LocalZarrSignalStore,
    settings: Settings,
    job_id: str,
) -> None:
    session = factory()
    output_version_id = str(uuid.uuid4())
    try:
        job = session.get(Job, job_id)
        if job is None:
            raise RuntimeError("job is missing")
        run = session.scalar(select(ProcessingRun).where(ProcessingRun.job_id == job.id))
        if run is None:
            raise RuntimeError("processing run is missing")
        recording = session.get(Recording, run.recording_id)
        if recording is None or recording.state != RecordingState.READY:
            raise NeuroError(
                "recording_not_ready", "The recording is not ready for processing.", 409
            )
        if job.status == JobState.SUCCEEDED:
            return
        job.status = JobState.RUNNING
        job.attempt += 1
        job.progress = 0.1
        job.stage = "Processing"
        run.status = "RUNNING"
        session.commit()
        if run.operation_id != "average_reference":
            raise NeuroError(
                "unsupported_operation", "This processing operation is not supported.", 422
            )
        indices = [int(value) for value in run.parameters.get("channel_indices", [])]
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
        if recording.state in {RecordingState.DELETING, RecordingState.DELETED}:
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
            manifest_sha256=_sha256_file(
                signal_store.version_path(output_version_id) / "manifest.json"
            ),
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
        job.status = JobState.SUCCEEDED
        job.progress = 1.0
        job.stage = "Ready"
        session.commit()
    except BaseException as exc:
        session.rollback()
        signal_store.delete(output_version_id)
        code, message = _safe_error(exc)
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobState.FAILED
            job.stage = "Failed"
            job.error_code = code
            job.error_message = message
        run = session.scalar(select(ProcessingRun).where(ProcessingRun.job_id == job_id))
        if run is not None:
            run.status = "FAILED"
        session.commit()
        raise
    finally:
        session.close()


def execute_deletion_job(
    *,
    factory: sessionmaker[Session],
    object_store: ObjectStore,
    signal_store: LocalZarrSignalStore,
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
        job.status = JobState.RUNNING
        job.stage = "Deleting"
        job.attempt += 1
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
        session.commit()
    except BaseException as exc:
        session.rollback()
        code, message = _safe_error(exc)
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobState.FAILED
            job.error_code = code
            job.error_message = message
        session.commit()
        raise
    finally:
        session.close()
