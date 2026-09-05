"""Deterministic, legally reusable synthetic sample for zero-friction evaluation."""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from neuro_workspace.db.models import (
    Channel,
    Event,
    Membership,
    Project,
    Recording,
    RecordingVersion,
    User,
    Workspace,
)
from neuro_workspace.domain import (
    ChannelMetadata,
    EventMetadata,
    RecordingMetadata,
    RecordingState,
    Role,
    SignalChunk,
)
from neuro_workspace.signal_store import ZarrSignalStore

SAMPLE_SLUG = "neuroworkspace-public-demo"


def _chunks(
    channel_count: int, sample_count: int, sampling_frequency_hz: float, chunk_size: int
) -> Iterator[SignalChunk]:
    rng = np.random.default_rng(20260818)
    phases = np.linspace(0, math.pi, channel_count, dtype=np.float64)
    for start in range(0, sample_count, chunk_size):
        stop = min(start + chunk_size, sample_count)
        time = np.arange(start, stop, dtype=np.float64) / sampling_frequency_hz
        values = np.empty((channel_count, stop - start), dtype=np.float32)
        for channel in range(channel_count):
            alpha = (18e-6 + channel * 0.3e-6) * np.sin(
                2 * np.pi * (9.5 + channel * 0.05) * time + phases[channel]
            )
            drift = 6e-6 * np.sin(2 * np.pi * 0.25 * time + phases[channel] / 2)
            line = 1.5e-6 * np.sin(2 * np.pi * 60 * time)
            noise = rng.normal(0, 2.2e-6, stop - start)
            values[channel] = (alpha + drift + line + noise).astype(np.float32)
        # Deterministic eye-blink-like review example across frontal channels.
        for onset in (30.0, 92.0, 181.5, 246.0):
            relative = time - onset
            pulse = 120e-6 * np.exp(-((relative / 0.12) ** 2))
            values[0] += pulse.astype(np.float32)
            values[1] += (pulse * 0.8).astype(np.float32)
        yield SignalChunk(start, values)


def seed_sample(
    factory: sessionmaker[Session], signal_store: ZarrSignalStore, build_version: str
) -> str:
    """Create the shared read-only synthetic demo once and return its recording ID."""

    session = factory()
    try:
        existing = session.scalar(
            select(Recording).where(
                Recording.is_demo.is_(True), Recording.state == RecordingState.READY
            )
        )
        if existing is not None:
            return existing.id
        system_user = session.scalar(select(User).where(User.external_subject == "system:sample"))
        if system_user is None:
            system_user = User(
                external_subject="system:sample",
                email="sample@localhost.invalid",
                display_name="NeuroWorkspace sample generator",
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
                Project.workspace_id == workspace.id, Project.name == "Sample recordings"
            )
        )
        if project is None:
            project = Project(
                workspace_id=workspace.id,
                name="Sample recordings",
                description="Public synthetic data for evaluating the viewer.",
            )
            session.add(project)
            session.flush()
        recording = Recording(
            workspace_id=workspace.id,
            project_id=project.id,
            creator_id=system_user.id,
            name="Eyes-open alpha rhythm — synthetic sample",
            format_id="synthetic",
            state=RecordingState.PROCESSING,
            is_demo=True,
        )
        session.add(recording)
        session.flush()
        version_id = str(uuid.uuid4())
        channel_names = (
            "Fp1",
            "Fp2",
            "F3",
            "F4",
            "C3",
            "C4",
            "P3",
            "P4",
            "O1",
            "O2",
            "F7",
            "F8",
            "T7",
            "T8",
            "P7",
            "P8",
        )
        sfreq = 256.0
        duration = 300.0
        sample_count = int(sfreq * duration)
        events = tuple(
            EventMetadata(onset, 0.3, "Synthetic blink example", source_index=index)
            for index, onset in enumerate((30.0, 92.0, 181.5, 246.0))
        )
        metadata = RecordingMetadata(
            format_id="synthetic",
            sampling_frequency_hz=sfreq,
            sample_count=sample_count,
            duration_seconds=duration,
            channels=tuple(
                ChannelMetadata(index, name, name, "eeg", "V", "V", 1.0, sfreq)
                for index, name in enumerate(channel_names)
            ),
            events=events,
            safe_extra={
                "attribution": (
                    "Deterministically generated by NeuroWorkspace; Apache-2.0 project asset."
                ),
                "public_sample": True,
                "contains_human_data": False,
                "limitations": (
                    "Illustrative waveforms are not a physiological reference or "
                    "validation dataset."
                ),
            },
        )
        manifest = signal_store.write(
            version_id,
            metadata,
            _chunks(len(channel_names), sample_count, sfreq, 8192),
            source_hashes=(),
            software={"neuroworkspace": build_version, "generator": "synthetic-alpha-v1"},
            chunk_samples=8192,
        )
        version = RecordingVersion(
            id=version_id,
            recording_id=recording.id,
            kind="normalized_raw",
            store_key=version_id,
            sampling_frequency_hz=sfreq,
            sample_count=sample_count,
            duration_seconds=duration,
            channel_count=len(channel_names),
            software=manifest.software,
            ready_at=datetime.now(UTC),
        )
        session.add(version)
        session.flush()
        for channel in metadata.channels:
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
        for event in events:
            session.add(
                Event(
                    version_id=version.id,
                    onset_seconds=event.onset_seconds,
                    duration_seconds=event.duration_seconds,
                    label=event.label,
                    source_index=event.source_index,
                )
            )
        recording.active_version_id = version.id
        recording.state = RecordingState.READY
        recording.safe_metadata = {
            "sampling_frequency_hz": sfreq,
            "sample_count": sample_count,
            "duration_seconds": duration,
            "channel_count": len(channel_names),
            "event_count": len(events),
            **metadata.safe_extra,
        }
        recording.source_provenance = {
            "kind": "synthetic",
            "generator": "synthetic-alpha-v1",
            "software": manifest.software,
        }
        session.commit()
        return recording.id
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
