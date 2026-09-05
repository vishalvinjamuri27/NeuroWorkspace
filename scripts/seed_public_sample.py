"""Fetch, checksum, ingest, and publish the pinned CC0 OpenNeuro alpha sample."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from fetch_test_data import destination_for, fetch_file, load_manifest
from neuro_workspace.adapters.registry import default_registry
from neuro_workspace.config import get_settings
from neuro_workspace.db.models import (
    Base,
    Job,
    Membership,
    Project,
    Recording,
    SourceAsset,
    Upload,
    User,
    Workspace,
)
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.domain import AssetRole, JobKind, JobState, RecordingState, Role
from neuro_workspace.services.jobs import execute_ingestion_job
from neuro_workspace.signal_store import create_signal_store
from neuro_workspace.storage import create_object_store

SAMPLE_ID = "openneuro_ds004362_eeglab"
SAMPLE_SLUG = "neuroworkspace-public-demo"
SAFE_ASSET_NAME = "openneuro-motor-imagery.set"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = load_manifest(root)
    cache = (root / str(manifest["cache_directory"])).resolve()
    sample = dict(manifest["samples"][SAMPLE_ID])
    file_spec = next(
        dict(item) for item in sample["files"] if str(item["path"]).endswith("_eeg.set")
    )
    source = destination_for(cache, file_spec)

    settings = get_settings()
    engine, factory = create_engine_and_factory(settings.database_url)
    Base.metadata.create_all(engine)
    object_store = create_object_store(settings)
    signal_store = create_signal_store(settings)
    session = factory()
    try:
        demos = session.scalars(select(Recording).where(Recording.is_demo.is_(True))).all()
        existing = next(
            (
                row
                for row in demos
                if row.state == RecordingState.READY
                and row.source_provenance.get("dataset") == SAMPLE_ID
                and row.active_version_id
                and signal_store.exists(row.active_version_id)
            ),
            None,
        )
        if existing is not None:
            print(f"Public sample recording ready: {existing.id}")
            return

        fetch_file(file_spec, source)

        system_user = session.scalar(select(User).where(User.external_subject == "system:sample"))
        if system_user is None:
            system_user = User(
                external_subject="system:sample",
                email="sample@localhost.invalid",
                display_name="NeuroWorkspace sample importer",
            )
            session.add(system_user)
            session.flush()
        workspace = session.scalar(select(Workspace).where(Workspace.slug == SAMPLE_SLUG))
        if workspace is None:
            workspace = Workspace(name="Public sample", slug=SAMPLE_SLUG)
            session.add(workspace)
            session.flush()
            session.add(
                Membership(
                    workspace_id=workspace.id,
                    user_id=system_user.id,
                    role=Role.OWNER,
                )
            )
        project = session.scalar(
            select(Project).where(
                Project.workspace_id == workspace.id,
                Project.name == "Sample recordings",
            )
        )
        if project is None:
            project = Project(
                workspace_id=workspace.id,
                name="Sample recordings",
                description="Attributed public data for evaluating the EEG workspace.",
            )
            session.add(project)
            session.flush()

        upload = Upload(
            workspace_id=workspace.id,
            project_id=project.id,
            creator_id=system_user.id,
            status="COMPLETED",
            format_hint="eeglab",
            total_bytes=source.stat().st_size,
        )
        session.add(upload)
        session.flush()
        recording = Recording(
            workspace_id=workspace.id,
            project_id=project.id,
            creator_id=system_user.id,
            name="Motor imagery EEG — OpenNeuro public sample",
            format_id="eeglab",
            state=RecordingState.QUEUED,
            is_demo=True,
        )
        session.add(recording)
        session.flush()
        asset_id = str(uuid.uuid4())
        object_key = f"uploads/{upload.id}/{asset_id}/{SAFE_ASSET_NAME}"
        info = object_store.put_file(object_key, source)
        session.add(
            SourceAsset(
                id=asset_id,
                upload_id=upload.id,
                recording_id=recording.id,
                object_key=object_key,
                safe_name=SAFE_ASSET_NAME,
                role=AssetRole.PRIMARY,
                size_bytes=info.size_bytes,
                sha256=info.sha256,
                media_type="application/octet-stream",
            )
        )
        job = Job(
            workspace_id=workspace.id,
            kind=JobKind.INGESTION,
            subject_type="recording",
            subject_id=recording.id,
            idempotency_key=f"sample:{recording.id}",
            stage="Inspecting",
        )
        session.add(job)
        session.commit()
        job_id = job.id
        recording_id = recording.id
    finally:
        session.close()

    try:
        execute_ingestion_job(
            factory=factory,
            object_store=object_store,
            signal_store=signal_store,
            registry=default_registry,
            settings=settings,
            job_id=job_id,
        )
        session = factory()
        try:
            recording = session.get(Recording, recording_id)
            job = session.get(Job, job_id)
            if recording is None or job is None or job.status != JobState.SUCCEEDED:
                raise RuntimeError("public sample ingestion did not complete")
            for other in session.scalars(
                select(Recording).where(Recording.is_demo.is_(True), Recording.id != recording.id)
            ):
                other.is_demo = False
            recording.name = "Motor imagery EEG — OpenNeuro public sample"
            recording.safe_metadata = {
                **recording.safe_metadata,
                "attribution": (
                    "OpenNeuro ds004362 v1.0.0, DOI 10.18112/openneuro.ds004362.v1.0.0; "
                    "dataset declared CC0."
                ),
                "public_sample": True,
                "contains_human_data": True,
                "limitations": (
                    "Public research data for software evaluation; not for diagnosis or "
                    "clinical interpretation. Subject fields are not displayed."
                ),
            }
            recording.source_provenance = {
                **recording.source_provenance,
                "kind": "public",
                "dataset": SAMPLE_ID,
                "source": "https://openneuro.org/datasets/ds004362/versions/1.0.0",
                "doi": "10.18112/openneuro.ds004362.v1.0.0",
                "license": "CC0",
                "pinned_sha256": file_spec["sha256"],
            }
            session.commit()
            print(f"Public sample recording ready: {recording.id}")
        finally:
            session.close()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
