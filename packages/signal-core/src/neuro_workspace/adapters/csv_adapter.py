"""Explicit-schema streaming adapter for delimited signal text."""

from __future__ import annotations

import csv
import math
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np

from neuro_workspace.domain import (
    AssetProbe,
    ChannelMetadata,
    CSVImportConfig,
    Detection,
    ImportConfig,
    RecordingMetadata,
    SignalChunk,
)
from neuro_workspace.errors import ValidationError

UNIT_TO_SI = {
    "V": 1.0,
    "mV": 1e-3,
    "uV": 1e-6,
    "µV": 1e-6,
    "nV": 1e-9,
    "1": 1.0,
}


class CSVAdapter:
    format_id = "csv"
    extensions = (".csv", ".tsv", ".txt")

    def detect(self, assets: Sequence[AssetProbe]) -> Detection | None:
        matches = [asset for asset in assets if Path(asset.name).suffix.lower() in self.extensions]
        if len(matches) != 1:
            return None
        return Detection(
            self.format_id, 0.45, "delimited text extension; schema confirmation required"
        )

    def _config(self, config: ImportConfig) -> CSVImportConfig:
        if not isinstance(config, CSVImportConfig):
            raise ValidationError(
                "csv_config_required",
                "Delimited text needs a sampling rate or time column, channel columns, "
                "names, and units.",
            )
        if not config.channel_columns:
            raise ValidationError("missing_channels", "Select at least one signal channel column.")
        if len(config.channel_columns) != len(set(config.channel_columns)):
            raise ValidationError(
                "duplicate_channel", "Each channel column may be selected only once."
            )
        if len(config.channel_columns) != len(config.channel_names) or len(config.units) != len(
            config.channel_columns
        ):
            raise ValidationError(
                "invalid_csv_config", "Channel columns, names, and units must align."
            )
        if len(config.channel_names) != len(set(config.channel_names)):
            raise ValidationError("duplicate_channel_name", "Channel names must be unique.")
        if config.sampling_frequency_hz is None and config.time_column is None:
            raise ValidationError(
                "missing_timebase",
                "Provide a sampling frequency or select a timestamp/time column.",
            )
        if config.sampling_frequency_hz is not None and config.sampling_frequency_hz <= 0:
            raise ValidationError("invalid_sampling_rate", "Sampling frequency must be positive.")
        unknown_units = [unit for unit in config.units if unit not in UNIT_TO_SI]
        if unknown_units:
            raise ValidationError(
                "unsupported_unit",
                "Map every channel to V, mV, uV/µV, nV, or unitless (1).",
                {"units": unknown_units},
            )
        return config

    def _primary(self, assets: Sequence[Path]) -> Path:
        matches = [path.resolve() for path in assets if path.suffix.lower() in self.extensions]
        if len(matches) != 1 or not matches[0].is_file():
            raise ValidationError(
                "ambiguous_primary_file", "Include exactly one CSV/TSV text file."
            )
        return matches[0]

    def _rows(
        self, path: Path, config: CSVImportConfig
    ) -> Iterator[tuple[list[float], float | None]]:
        delimiter = (
            "\t" if path.suffix.lower() == ".tsv" and config.delimiter == "," else config.delimiter
        )
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter=delimiter)
            if reader.fieldnames is None:
                raise ValidationError("malformed_header", "The delimited file has no header row.")
            required = set(config.channel_columns)
            if config.time_column:
                required.add(config.time_column)
            missing = sorted(required - set(reader.fieldnames))
            if missing:
                raise ValidationError(
                    "missing_column",
                    "The configured columns are not present.",
                    {"columns": missing},
                )
            for row_index, row in enumerate(reader, start=2):
                try:
                    channel_values = [float(row[column]) for column in config.channel_columns]
                    time_value = float(row[config.time_column]) if config.time_column else None
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        "invalid_numeric_value",
                        f"Row {row_index} contains a non-numeric configured signal/time value.",
                        {"row": row_index},
                    ) from exc
                yield channel_values, time_value

    def validate(self, assets: Sequence[Path], config: ImportConfig = None) -> None:
        selected = self._config(config)
        path = self._primary(assets)
        count = 0
        previous_time: float | None = None
        for values, time_value in self._rows(path, selected):
            count += 1
            if not all(math.isfinite(value) or math.isnan(value) for value in values):
                raise ValidationError("invalid_signal", "Signal values may be finite or NaN only.")
            if time_value is not None:
                if not math.isfinite(time_value) or (
                    previous_time is not None and time_value <= previous_time
                ):
                    raise ValidationError(
                        "inconsistent_timebase",
                        "The selected time column must be finite and increasing.",
                    )
                previous_time = time_value
        if count < 2:
            raise ValidationError("insufficient_rows", "At least two signal rows are required.")

    def _inspect_timebase(
        self, assets: Sequence[Path], config: CSVImportConfig
    ) -> tuple[int, float]:
        count = 0
        first_time: float | None = None
        last_time: float | None = None
        previous_time: float | None = None
        deltas: list[float] = []
        for _, time_value in self._rows(self._primary(assets), config):
            count += 1
            if time_value is not None:
                if previous_time is not None:
                    deltas.append(time_value - previous_time)
                first_time = time_value if first_time is None else first_time
                last_time = time_value
                previous_time = time_value
        if count < 2:
            raise ValidationError("insufficient_rows", "At least two signal rows are required.")
        if config.sampling_frequency_hz is not None:
            sfreq = config.sampling_frequency_hz
        else:
            positive = np.asarray([delta for delta in deltas if delta > 0], dtype=np.float64)
            if positive.size != len(deltas) or positive.size == 0:
                raise ValidationError(
                    "inconsistent_timebase", "Time values must increase uniformly."
                )
            median_delta = float(np.median(positive))
            if np.max(np.abs(positive - median_delta)) > max(1e-9, median_delta * 1e-3):
                raise ValidationError(
                    "inconsistent_timebase",
                    "The time column is irregular; provide an explicit resampling workflow.",
                )
            sfreq = 1.0 / median_delta
        if first_time is not None and last_time is not None and last_time <= first_time:
            raise ValidationError("inconsistent_timebase", "Time values must increase.")
        return count, sfreq

    def inspect_metadata(
        self, assets: Sequence[Path], config: ImportConfig = None
    ) -> RecordingMetadata:
        selected = self._config(config)
        self.validate(assets, selected)
        count, sfreq = self._inspect_timebase(assets, selected)
        types = selected.channel_types or tuple("eeg" for _ in selected.channel_columns)
        if len(types) != len(selected.channel_columns):
            raise ValidationError("invalid_csv_config", "Channel types must align with channels.")
        channels = tuple(
            ChannelMetadata(
                index=index,
                name=selected.channel_names[index],
                source_name=column,
                channel_type=types[index],
                unit="V" if selected.units[index] != "1" else "1",
                source_unit=selected.units[index],
                conversion_to_si=UNIT_TO_SI[selected.units[index]],
                source_sampling_frequency_hz=sfreq,
            )
            for index, column in enumerate(selected.channel_columns)
        )
        return RecordingMetadata(
            format_id=self.format_id,
            sampling_frequency_hz=sfreq,
            sample_count=count,
            duration_seconds=count / sfreq,
            channels=channels,
            safe_extra={"time_column": selected.time_column, "delimiter": selected.delimiter},
        )

    def iter_signal_chunks(
        self,
        assets: Sequence[Path],
        chunk_samples: int,
        config: ImportConfig = None,
    ) -> Iterator[SignalChunk]:
        selected = self._config(config)
        if chunk_samples <= 0:
            raise ValueError("chunk_samples must be positive")
        factors = np.asarray([UNIT_TO_SI[unit] for unit in selected.units], dtype=np.float64)
        rows: list[list[float]] = []
        start = 0
        for values, _ in self._rows(self._primary(assets), selected):
            rows.append(values)
            if len(rows) == chunk_samples:
                array = (np.asarray(rows, dtype=np.float64) * factors).T.astype(np.float32)
                yield SignalChunk(start, array)
                start += len(rows)
                rows.clear()
        if rows:
            array = (np.asarray(rows, dtype=np.float64) * factors).T.astype(np.float32)
            yield SignalChunk(start, array)
