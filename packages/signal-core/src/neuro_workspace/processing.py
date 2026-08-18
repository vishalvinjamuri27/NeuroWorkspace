"""Versioned, non-destructive processing operations."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

import numpy as np

from neuro_workspace.domain import ChannelMetadata, EventMetadata, RecordingMetadata, SignalChunk
from neuro_workspace.errors import ValidationError
from neuro_workspace.signal_store import LocalZarrSignalStore, SignalManifest


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    metadata: RecordingMetadata
    manifest: SignalManifest
    provenance: dict[str, object]


def _average_reference_chunks(
    source: LocalZarrSignalStore,
    version_id: str,
    sample_count: int,
    channel_indices: Sequence[int],
    chunk_samples: int,
) -> Iterator[SignalChunk]:
    if not channel_indices:
        raise ValidationError(
            "missing_reference_channels", "Select channels for average reference."
        )
    for start in range(0, sample_count, chunk_samples):
        stop = min(start + chunk_samples, sample_count)
        values = source.read_raw_window(version_id, start, stop)
        reference = np.nanmean(values[list(channel_indices)], axis=0, keepdims=True)
        yield SignalChunk(start, (values - reference).astype(np.float32))


def average_reference(
    *,
    signal_store: LocalZarrSignalStore,
    input_version_id: str,
    output_version_id: str,
    reference_channel_indices: Sequence[int],
    build_version: str,
) -> ProcessingResult:
    """Create an immutable common-average-referenced version in bounded chunks."""

    manifest = signal_store.validate(input_version_id)
    metadata_dict = signal_store.metadata(input_version_id)
    channels = tuple(ChannelMetadata(**channel) for channel in metadata_dict["channels"])
    events = tuple(EventMetadata(**event) for event in metadata_dict.get("events", []))
    metadata = RecordingMetadata(
        format_id=str(metadata_dict["format_id"]),
        sampling_frequency_hz=float(metadata_dict["sampling_frequency_hz"]),
        sample_count=int(metadata_dict["sample_count"]),
        duration_seconds=float(metadata_dict["duration_seconds"]),
        channels=tuple(replace(channel) for channel in channels),
        events=events,
        safe_extra={
            **dict(metadata_dict.get("safe_extra", {})),
            "reference": "common average",
            "reference_channel_indices": list(reference_channel_indices),
        },
        warnings=tuple(metadata_dict.get("warnings", [])),
    )
    output_manifest = signal_store.write(
        output_version_id,
        metadata,
        _average_reference_chunks(
            signal_store,
            input_version_id,
            manifest.sample_count,
            reference_channel_indices,
            manifest.raw_chunks[1],
        ),
        source_hashes=manifest.source_hashes,
        software={**manifest.software, "neuroworkspace": build_version},
        chunk_samples=manifest.raw_chunks[1],
    )
    return ProcessingResult(
        metadata,
        output_manifest,
        {
            "operation": "average_reference",
            "version": "1",
            "input_version_id": input_version_id,
            "parameters": {"channel_indices": list(reference_channel_indices)},
            "software": output_manifest.software,
        },
    )
