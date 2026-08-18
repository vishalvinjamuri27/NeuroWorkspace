"""Immutable Zarr signal versions and bounded waveform queries."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import zarr
from numpy.typing import NDArray

from neuro_workspace.domain import RecordingMetadata, SignalChunk
from neuro_workspace.errors import ValidationError
from neuro_workspace.pyramid import Envelope, choose_resolution, combine_envelope, envelope_from_raw

PYRAMID_FACTOR = 4
LAYOUT_VERSION = 1


@dataclass(frozen=True, slots=True)
class SignalManifest:
    layout_version: int
    format_id: str
    sampling_frequency_hz: float
    sample_count: int
    channel_count: int
    raw_dtype: str
    raw_chunks: tuple[int, int]
    pyramid_factor: int
    pyramid_levels: int
    source_hashes: tuple[str, ...]
    software: dict[str, str]


@dataclass(frozen=True, slots=True)
class WaveformChannel:
    index: int
    name: str
    unit: str
    minimum: list[float | None]
    maximum: list[float | None]
    valid_count: list[int]


@dataclass(frozen=True, slots=True)
class WaveformWindow:
    start_seconds: float
    end_seconds: float
    level: int
    samples_per_bin: int
    bin_start_seconds: list[float]
    channels: tuple[WaveformChannel, ...]


def _to_optional_float(values: NDArray[np.floating]) -> list[float | None]:
    return [float(value) if np.isfinite(value) else None for value in values]


class LocalZarrSignalStore:
    """Local path implementation; immutable directories can be synced to object storage."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def version_path(self, version_id: str) -> Path:
        if not version_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in version_id
        ):
            raise ValidationError("invalid_version", "The recording version identifier is invalid.")
        path = (self.root / version_id).resolve()
        if self.root not in path.parents:
            raise ValidationError("invalid_version", "The recording version identifier is invalid.")
        return path

    def write(
        self,
        version_id: str,
        metadata: RecordingMetadata,
        chunks: Iterator[SignalChunk],
        *,
        source_hashes: tuple[str, ...] = (),
        software: dict[str, str] | None = None,
        chunk_samples: int = 262_144,
    ) -> SignalManifest:
        target = self.version_path(version_id)
        if target.exists():
            raise ValidationError(
                "version_exists", "The immutable recording version already exists."
            )
        target.mkdir(parents=True)
        try:
            group = zarr.open_group(str(target / "signals.zarr"), mode="w", zarr_format=3)
            channel_count = len(metadata.channels)
            time_chunk = min(max(1024, chunk_samples), max(1024, metadata.sample_count))
            raw = group.create_array(
                "raw",
                shape=(channel_count, metadata.sample_count),
                chunks=(1, time_chunk),
                dtype="f4",
                fill_value=np.nan,
            )
            expected_start = 0
            for chunk in chunks:
                if chunk.start_sample != expected_start:
                    raise ValidationError(
                        "noncontiguous_chunks", "The signal adapter returned non-contiguous chunks."
                    )
                if chunk.values.shape[0] != channel_count:
                    raise ValidationError(
                        "invalid_chunk_shape", "A signal chunk has the wrong channel count."
                    )
                if chunk.stop_sample > metadata.sample_count:
                    raise ValidationError(
                        "invalid_chunk_shape", "A signal chunk exceeds the recording length."
                    )
                raw[:, chunk.start_sample : chunk.stop_sample] = chunk.values
                expected_start = chunk.stop_sample
            if expected_start != metadata.sample_count:
                raise ValidationError(
                    "incomplete_signal", "The adapter did not produce every signal sample."
                )

            levels = self._build_pyramid(group, metadata, time_chunk)
            manifest = SignalManifest(
                layout_version=LAYOUT_VERSION,
                format_id=metadata.format_id,
                sampling_frequency_hz=metadata.sampling_frequency_hz,
                sample_count=metadata.sample_count,
                channel_count=channel_count,
                raw_dtype="float32",
                raw_chunks=(1, time_chunk),
                pyramid_factor=PYRAMID_FACTOR,
                pyramid_levels=levels,
                source_hashes=source_hashes,
                software=software or {},
            )
            (target / "metadata.json").write_text(
                json.dumps(
                    {
                        "format_id": metadata.format_id,
                        "sampling_frequency_hz": metadata.sampling_frequency_hz,
                        "sample_count": metadata.sample_count,
                        "duration_seconds": metadata.duration_seconds,
                        "channels": [asdict(channel) for channel in metadata.channels],
                        "events": [asdict(event) for event in metadata.events],
                        "safe_extra": metadata.safe_extra,
                        "warnings": metadata.warnings,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            (target / "manifest.json").write_text(
                json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            self.validate(version_id)
            return manifest
        except BaseException:
            import shutil

            shutil.rmtree(target, ignore_errors=True)
            raise

    def _build_pyramid(self, group: Any, metadata: RecordingMetadata, time_chunk: int) -> int:
        source_kind = "raw"
        source_bins = metadata.sample_count
        level = 0
        while source_bins > 1:
            level += 1
            target_bins = math.ceil(source_bins / PYRAMID_FACTOR)
            level_group = group.create_group(f"envelopes/L{level}")
            minimum = level_group.create_array(
                "minimum",
                shape=(len(metadata.channels), target_bins),
                chunks=(1, min(time_chunk, target_bins)),
                dtype="f4",
                fill_value=np.nan,
            )
            maximum = level_group.create_array(
                "maximum",
                shape=(len(metadata.channels), target_bins),
                chunks=(1, min(time_chunk, target_bins)),
                dtype="f4",
                fill_value=np.nan,
            )
            valid_count = level_group.create_array(
                "valid_count",
                shape=(len(metadata.channels), target_bins),
                chunks=(1, min(time_chunk, target_bins)),
                dtype="i4",
                fill_value=0,
            )
            output_chunk = max(1, time_chunk // PYRAMID_FACTOR)
            for start in range(0, target_bins, output_chunk):
                stop = min(start + output_chunk, target_bins)
                source_start = start * PYRAMID_FACTOR
                source_stop = min(stop * PYRAMID_FACTOR, source_bins)
                if source_kind == "raw":
                    envelope = envelope_from_raw(
                        np.asarray(group["raw"][:, source_start:source_stop]), PYRAMID_FACTOR
                    )
                else:
                    previous = group[f"envelopes/L{level - 1}"]
                    envelope = combine_envelope(
                        Envelope(
                            minimum=np.asarray(previous["minimum"][:, source_start:source_stop]),
                            maximum=np.asarray(previous["maximum"][:, source_start:source_stop]),
                            valid_count=np.asarray(
                                previous["valid_count"][:, source_start:source_stop]
                            ),
                        ),
                        PYRAMID_FACTOR,
                    )
                actual_stop = start + envelope.minimum.shape[1]
                minimum[:, start:actual_stop] = envelope.minimum
                maximum[:, start:actual_stop] = envelope.maximum
                valid_count[:, start:actual_stop] = envelope.valid_count
            source_kind = "envelope"
            source_bins = target_bins
            if level >= 16:
                break
        return level

    def validate(self, version_id: str) -> SignalManifest:
        target = self.version_path(version_id)
        manifest_data = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        manifest_data["raw_chunks"] = tuple(manifest_data["raw_chunks"])
        manifest_data["source_hashes"] = tuple(manifest_data["source_hashes"])
        manifest = SignalManifest(**manifest_data)
        group = zarr.open_group(str(target / "signals.zarr"), mode="r")
        raw = cast(zarr.Array[Any], group["raw"])
        if tuple(raw.shape) != (manifest.channel_count, manifest.sample_count):
            raise ValidationError(
                "invalid_store", "The canonical signal shape does not match its manifest."
            )
        if manifest.sample_count and manifest.channel_count:
            _ = np.asarray(raw[0, : min(4, manifest.sample_count)])
        return manifest

    def metadata(self, version_id: str) -> dict[str, Any]:
        return dict(
            json.loads(
                (self.version_path(version_id) / "metadata.json").read_text(encoding="utf-8")
            )
        )

    def query(
        self,
        version_id: str,
        channel_indices: list[int],
        start_seconds: float,
        duration_seconds: float,
        pixel_width: int,
    ) -> WaveformWindow:
        manifest = self.validate(version_id)
        metadata = self.metadata(version_id)
        if not channel_indices or len(channel_indices) > manifest.channel_count:
            raise ValidationError("invalid_channels", "Choose one or more available channels.")
        if (
            len(set(channel_indices)) != len(channel_indices)
            or min(channel_indices) < 0
            or max(channel_indices) >= manifest.channel_count
        ):
            raise ValidationError(
                "invalid_channels", "A selected channel is invalid or duplicated."
            )
        if (
            not np.isfinite(start_seconds)
            or not np.isfinite(duration_seconds)
            or duration_seconds <= 0
        ):
            raise ValidationError("invalid_time_range", "Choose a finite, positive time window.")
        start_sample = max(
            0,
            min(
                manifest.sample_count,
                math.floor(start_seconds * manifest.sampling_frequency_hz),
            ),
        )
        stop_sample = max(
            start_sample,
            min(
                manifest.sample_count,
                math.ceil((start_seconds + duration_seconds) * manifest.sampling_frequency_hz),
            ),
        )
        if stop_sample == start_sample:
            raise ValidationError(
                "invalid_time_range", "The selected time window contains no samples."
            )
        level, samples_per_bin = choose_resolution(stop_sample - start_sample, pixel_width)
        level = min(level, manifest.pyramid_levels)
        samples_per_bin = PYRAMID_FACTOR**level
        group = zarr.open_group(str(self.version_path(version_id) / "signals.zarr"), mode="r")
        if level == 0:
            raw_array = cast(zarr.Array[Any], group["raw"])
            channel_selection = np.asarray(channel_indices, dtype=np.int64)
            raw_values = np.asarray(
                raw_array.oindex[channel_selection, slice(start_sample, stop_sample)]
            )
            minimum = raw_values
            maximum = raw_values
            valid_count = np.isfinite(raw_values).astype(np.int32)
            first_bin = start_sample
        else:
            first_bin = start_sample // samples_per_bin
            last_bin = math.ceil(stop_sample / samples_per_bin)
            selected = cast(zarr.Group, group[f"envelopes/L{level}"])
            minimum_array = cast(zarr.Array[Any], selected["minimum"])
            maximum_array = cast(zarr.Array[Any], selected["maximum"])
            valid_array = cast(zarr.Array[Any], selected["valid_count"])
            selection = (
                np.asarray(channel_indices, dtype=np.int64),
                slice(first_bin, last_bin),
            )
            minimum = np.asarray(minimum_array.oindex[selection])
            maximum = np.asarray(maximum_array.oindex[selection])
            valid_count = np.asarray(valid_array.oindex[selection])
        bin_starts = [
            (first_bin + index) * samples_per_bin / manifest.sampling_frequency_hz
            for index in range(minimum.shape[1])
        ]
        channels = tuple(
            WaveformChannel(
                index=channel_index,
                name=str(metadata["channels"][channel_index]["name"]),
                unit=str(metadata["channels"][channel_index]["unit"]),
                minimum=_to_optional_float(minimum[row]),
                maximum=_to_optional_float(maximum[row]),
                valid_count=[int(value) for value in valid_count[row]],
            )
            for row, channel_index in enumerate(channel_indices)
        )
        return WaveformWindow(
            start_seconds=start_sample / manifest.sampling_frequency_hz,
            end_seconds=stop_sample / manifest.sampling_frequency_hz,
            level=level,
            samples_per_bin=samples_per_bin,
            bin_start_seconds=bin_starts,
            channels=channels,
        )

    def read_raw_window(
        self, version_id: str, start_sample: int, stop_sample: int
    ) -> NDArray[np.float32]:
        manifest = self.validate(version_id)
        if start_sample < 0 or stop_sample < start_sample or stop_sample > manifest.sample_count:
            raise ValidationError("invalid_time_range", "The raw window is out of bounds.")
        group = zarr.open_group(str(self.version_path(version_id) / "signals.zarr"), mode="r")
        raw = cast(zarr.Array[Any], group["raw"])
        return np.asarray(raw[:, start_sample:stop_sample], dtype=np.float32)

    def delete(self, version_id: str) -> int:
        import shutil

        path = self.version_path(version_id)
        if not path.exists():
            return 0
        count = sum(1 for item in path.rglob("*") if item.is_file())
        shutil.rmtree(path)
        return count
