"""Create real, S3-backed EEG recordings of representative durations for the
viewport/S3/cache/load-test benchmarks in docs/PERFORMANCE.md.

Unlike scripts/seed_benchmark.py (DB-only rows, no backing signal data --
correct for exercising relational query plans, wrong for anything that
touches the waveform endpoint), this writes real canonical Zarr arrays
through the same `signal_store.write()` path ingestion uses, directly
against the Compose stack's MinIO bucket. The resulting recordings are
fully servable by the live API's /waveform, /qc-adjacent metadata, etc.

Marked `is_demo=True` (like the public sample) so they're readable without
a session -- these are synthetic benchmark fixtures, not anyone's private
data, and unauthenticated reads keep the k6/HTTP benchmark scripts simple.

    uv run python scripts/seed_signal_recordings.py
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterator

import numpy as np
from sqlalchemy import select

sys.path.insert(0, "packages/signal-core/src")

from neuro_workspace.config import get_settings
from neuro_workspace.db.models import (
    Base,
    Channel,
    Membership,
    Project,
    Recording,
    RecordingVersion,
    User,
    Workspace,
)
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.domain import (
    ChannelMetadata,
    RecordingMetadata,
    RecordingState,
    Role,
    SignalChunk,
)
from neuro_workspace.signal_store import create_signal_store

WORKSPACE_SLUG = "neuroworkspace-perf-bench"
CHANNEL_COUNT = 32
SAMPLING_HZ = 256.0

# (label, duration_seconds) -- matches section 8's "30 seconds / 5 minutes /
# 30 minutes" viewport-benchmark request.
DURATIONS = [
    ("30s", 30.0),
    ("5min", 5 * 60.0),
    ("30min", 30 * 60.0),
]


def generate_chunks(
    channel_count: int, sample_count: int, sampling_hz: float, chunk_samples: int
) -> Iterator[SignalChunk]:
    rng = np.random.default_rng(20260904)
    phases = np.linspace(0, math.pi, channel_count, dtype=np.float64)
    for start in range(0, sample_count, chunk_samples):
        stop = min(start + chunk_samples, sample_count)
        t = np.arange(start, stop, dtype=np.float64) / sampling_hz
        values = np.empty((channel_count, stop - start), dtype=np.float32)
        for ch in range(channel_count):
            alpha = 18e-6 * np.sin(2 * np.pi * (9.5 + ch * 0.05) * t + phases[ch])
            noise = rng.normal(0, 2e-6, stop - start)
            values[ch] = (alpha + noise).astype(np.float32)
        yield SignalChunk(start, values)


def main() -> None:
    settings = get_settings()
    engine, factory = create_engine_and_factory(settings.database_url)
    Base.metadata.create_all(engine)
    signal_store = create_signal_store(settings)
    session = factory()
    created_ids = {}
    try:
        system_user = session.scalar(
            select(User).where(User.external_subject == "system:perf-bench")
        )
        if system_user is None:
            system_user = User(
                external_subject="system:perf-bench",
                email="perf-bench@localhost.invalid",
                display_name="Performance benchmark fixtures",
            )
            session.add(system_user)
            session.flush()
        workspace = session.scalar(select(Workspace).where(Workspace.slug == WORKSPACE_SLUG))
        if workspace is None:
            workspace = Workspace(name="Performance benchmark fixtures", slug=WORKSPACE_SLUG)
            session.add(workspace)
            session.flush()
            session.add(
                Membership(workspace_id=workspace.id, user_id=system_user.id, role=Role.OWNER)
            )
        project = session.scalar(
            select(Project).where(
                Project.workspace_id == workspace.id, Project.name == "Viewport benchmarks"
            )
        )
        if project is None:
            project = Project(
                workspace_id=workspace.id,
                name="Viewport benchmarks",
                description="Synthetic recordings at fixed 32ch/256Hz for docs/PERFORMANCE.md.",
            )
            session.add(project)
            session.flush()

        for label, duration in DURATIONS:
            existing = session.scalar(
                select(Recording).where(
                    Recording.project_id == project.id,
                    Recording.name == f"perf-bench-{label}",
                )
            )
            if (
                existing is not None
                and existing.active_version_id
                and signal_store.exists(existing.active_version_id)
            ):
                print(f"{label}: already present -> recording_id={existing.id}")
                created_ids[label] = existing.id
                continue

            sample_count = int(duration * SAMPLING_HZ)
            version_id = f"perfbench-{label}-{system_user.id[:8]}"
            if signal_store.exists(version_id):
                # Leftover from an interrupted previous run of this script --
                # the object-store write and the DB commit below are not one
                # transaction, so a crash between them can strand a version.
                print(f"{label}: removing stale object-store version {version_id} first")
                signal_store.delete(version_id)
            metadata = RecordingMetadata(
                format_id="generated-perf-bench",
                sampling_frequency_hz=SAMPLING_HZ,
                sample_count=sample_count,
                duration_seconds=duration,
                channels=tuple(
                    ChannelMetadata(
                        index=i,
                        name=f"EEG{i:03d}",
                        source_name=f"EEG{i:03d}",
                        channel_type="eeg",
                        unit="V",
                        source_unit="V",
                        source_sampling_frequency_hz=SAMPLING_HZ,
                    )
                    for i in range(CHANNEL_COUNT)
                ),
            )
            print(
                f"{label}: writing {sample_count:,} samples x {CHANNEL_COUNT} channels "
                "to signal store ..."
            )
            manifest = signal_store.write(
                version_id,
                metadata,
                generate_chunks(CHANNEL_COUNT, sample_count, SAMPLING_HZ, 262_144),
            )

            recording = existing or Recording(
                workspace_id=workspace.id,
                project_id=project.id,
                creator_id=system_user.id,
                name=f"perf-bench-{label}",
                format_id="generated-perf-bench",
                is_demo=True,
            )
            recording.state = RecordingState.READY
            recording.active_version_id = version_id
            recording.safe_metadata = {
                "duration_seconds": duration,
                "sampling_frequency_hz": SAMPLING_HZ,
                "channel_count": CHANNEL_COUNT,
                "sample_count": sample_count,
                "pyramid_levels": manifest.pyramid_levels,
            }
            recording.source_provenance = {"kind": "synthetic-perf-bench"}
            if existing is None:
                session.add(recording)
            session.flush()

            version = session.get(RecordingVersion, version_id)
            if version is None:
                session.add(
                    RecordingVersion(
                        id=version_id,
                        recording_id=recording.id,
                        kind="normalized_raw",
                        store_key=version_id,
                        manifest_sha256=signal_store.manifest_sha256(version_id),
                        sampling_frequency_hz=SAMPLING_HZ,
                        sample_count=sample_count,
                        duration_seconds=duration,
                        channel_count=CHANNEL_COUNT,
                        software={"generator": "seed_signal_recordings"},
                    )
                )
                session.flush()
                for i in range(CHANNEL_COUNT):
                    session.add(
                        Channel(
                            version_id=version_id,
                            channel_index=i,
                            name=f"EEG{i:03d}",
                            source_name=f"EEG{i:03d}",
                            channel_type="eeg",
                            unit="V",
                            source_unit="V",
                            source_sampling_frequency_hz=SAMPLING_HZ,
                        )
                    )
            session.commit()
            created_ids[label] = recording.id
            print(
                f"{label}: recording_id={recording.id} version_id={version_id} "
                f"pyramid_levels={manifest.pyramid_levels}"
            )
    finally:
        session.close()

    print("\nDone. Recording ids:")
    for label, rid in created_ids.items():
        print(f"  {label}: {rid}")


if __name__ == "__main__":
    main()
