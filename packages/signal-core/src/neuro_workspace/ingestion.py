"""Framework-independent ingestion orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mne
import numpy as np
import zarr

from neuro_workspace.adapters.base import RecordingAdapter
from neuro_workspace.domain import ImportConfig, RecordingMetadata
from neuro_workspace.qc import ChannelQC, QCThresholds, compute_qc
from neuro_workspace.signal_store import LocalZarrSignalStore, SignalManifest


@dataclass(frozen=True, slots=True)
class IngestionResult:
    metadata: RecordingMetadata
    manifest: SignalManifest
    qc: tuple[ChannelQC, ...]


def choose_chunk_samples(channel_count: int, target_bytes: int = 16 * 1024 * 1024) -> int:
    """Choose a bounded float32 time chunk close to the byte target."""

    if channel_count <= 0:
        raise ValueError("channel_count must be positive")
    samples = target_bytes // (channel_count * np.dtype(np.float32).itemsize)
    return max(4096, min(1_048_576, samples))


def ingest_recording(
    *,
    adapter: RecordingAdapter,
    assets: list[Path],
    import_config: ImportConfig,
    version_id: str,
    signal_store: LocalZarrSignalStore,
    source_hashes: tuple[str, ...],
    build_version: str,
    qc_window_seconds: float = 300.0,
) -> IngestionResult:
    """Validate, convert, pyramid, and QC one immutable recording version."""

    adapter.validate(assets, import_config)
    metadata = adapter.inspect_metadata(assets, import_config)
    chunk_samples = choose_chunk_samples(len(metadata.channels))
    manifest = signal_store.write(
        version_id,
        metadata,
        adapter.iter_signal_chunks(assets, chunk_samples, import_config),
        source_hashes=source_hashes,
        software={
            "neuroworkspace": build_version,
            "mne": mne.__version__,
            "zarr": zarr.__version__,
        },
        chunk_samples=chunk_samples,
    )
    qc_samples = min(
        metadata.sample_count, max(1, int(qc_window_seconds * metadata.sampling_frequency_hz))
    )
    qc_values = signal_store.read_raw_window(version_id, 0, qc_samples)
    qc = compute_qc(qc_values, metadata.sampling_frequency_hz, QCThresholds())
    return IngestionResult(metadata=metadata, manifest=manifest, qc=qc)
