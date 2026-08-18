"""MNE-backed adapters for mature neurophysiology formats."""

from __future__ import annotations

import configparser
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import ClassVar

import mne
import numpy as np

from neuro_workspace.domain import (
    AssetProbe,
    ChannelMetadata,
    Detection,
    EventMetadata,
    ImportConfig,
    RecordingMetadata,
    SignalChunk,
)
from neuro_workspace.errors import ValidationError

UNIT_BY_CHANNEL_TYPE = {
    "eeg": "V",
    "eog": "V",
    "ecg": "V",
    "emg": "V",
    "seeg": "V",
    "ecog": "V",
    "dbs": "V",
    "bio": "V",
    "stim": "1",
    "misc": "unknown",
}


def _safe_paths(assets: Sequence[Path]) -> tuple[Path, ...]:
    paths = tuple(Path(asset).resolve() for asset in assets)
    if not paths:
        raise ValidationError("missing_file", "No recording file was included.")
    for path in paths:
        if not path.is_file():
            raise ValidationError(
                "missing_file", f"The required file {path.name} was not included."
            )
    return paths


def _find_extension(assets: Sequence[Path], extensions: tuple[str, ...]) -> Path:
    matches = [path for path in assets if path.suffix.lower() in extensions]
    if len(matches) != 1:
        names = ", ".join(extensions)
        raise ValidationError(
            "ambiguous_primary_file",
            f"Expected exactly one primary file with extension {names}.",
        )
    return matches[0]


class MNEAdapter:
    """Base class that preserves lazy MNE reads and normalizes safe metadata."""

    format_id: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]

    def detect(self, assets: Sequence[AssetProbe]) -> Detection | None:
        matches = [asset for asset in assets if Path(asset.name).suffix.lower() in self.extensions]
        if len(matches) != 1:
            return None
        path = Path(matches[0].path)
        confidence = 0.55
        reason = f"one {self.extensions[0]} candidate"
        try:
            header = path.read_bytes()[:16]
            if self._magic_matches(header):
                confidence = 0.95
                reason = "extension and file signature agree"
        except OSError:
            pass
        return Detection(self.format_id, confidence, reason)

    def _magic_matches(self, header: bytes) -> bool:
        return bool(header)

    def primary_path(self, assets: Sequence[Path]) -> Path:
        return _find_extension(_safe_paths(assets), self.extensions)

    def validate(self, assets: Sequence[Path], config: ImportConfig = None) -> None:
        del config
        raw = self._open(assets)
        try:
            if raw.n_times <= 0 or len(raw.ch_names) <= 0:
                raise ValidationError(
                    "empty_recording", "The recording contains no signal samples."
                )
            if not np.isfinite(float(raw.info["sfreq"])) or float(raw.info["sfreq"]) <= 0:
                raise ValidationError(
                    "invalid_sampling_rate", "The recording sampling rate is invalid."
                )
        finally:
            raw.close()

    def _open(self, assets: Sequence[Path]) -> mne.io.BaseRaw:
        return mne.io.read_raw(self.primary_path(assets), preload=False, verbose="ERROR")

    def inspect_metadata(
        self, assets: Sequence[Path], config: ImportConfig = None
    ) -> RecordingMetadata:
        del config
        raw = self._open(assets)
        try:
            sfreq = float(raw.info["sfreq"])
            channel_types = raw.get_channel_types()
            channels = tuple(
                ChannelMetadata(
                    index=index,
                    name=name,
                    source_name=name,
                    channel_type=channel_types[index],
                    unit=UNIT_BY_CHANNEL_TYPE.get(channel_types[index], "unknown"),
                    source_sampling_frequency_hz=sfreq,
                )
                for index, name in enumerate(raw.ch_names)
            )
            events = tuple(
                EventMetadata(
                    onset_seconds=float(onset),
                    duration_seconds=float(duration),
                    label=str(description),
                    source_index=index,
                )
                for index, (onset, duration, description) in enumerate(
                    zip(
                        raw.annotations.onset,
                        raw.annotations.duration,
                        raw.annotations.description,
                        strict=True,
                    )
                )
            )
            highpass = raw.info.get("highpass")
            lowpass = raw.info.get("lowpass")
            return RecordingMetadata(
                format_id=self.format_id,
                sampling_frequency_hz=sfreq,
                sample_count=int(raw.n_times),
                duration_seconds=float(raw.n_times) / sfreq,
                channels=channels,
                events=events,
                safe_extra={
                    "highpass_hz": float(highpass) if highpass is not None else None,
                    "lowpass_hz": float(lowpass) if lowpass is not None else None,
                    "bad_channels_from_source": list(raw.info.get("bads", [])),
                },
            )
        finally:
            raw.close()

    def iter_signal_chunks(
        self,
        assets: Sequence[Path],
        chunk_samples: int,
        config: ImportConfig = None,
    ) -> Iterator[SignalChunk]:
        del config
        if chunk_samples <= 0:
            raise ValueError("chunk_samples must be positive")
        raw = self._open(assets)
        try:
            for start in range(0, int(raw.n_times), chunk_samples):
                stop = min(start + chunk_samples, int(raw.n_times))
                yield SignalChunk(
                    start, np.asarray(raw.get_data(start=start, stop=stop), dtype=np.float32)
                )
        finally:
            raw.close()


class EDFAdapter(MNEAdapter):
    format_id = "edf"
    extensions = (".edf",)

    def _magic_matches(self, header: bytes) -> bool:
        return header[:8].strip() in {b"0", b""} and len(header) >= 8

    def _open(self, assets: Sequence[Path]) -> mne.io.BaseRaw:
        return mne.io.read_raw_edf(self.primary_path(assets), preload=False, verbose="ERROR")


class BDFAdapter(MNEAdapter):
    format_id = "bdf"
    extensions = (".bdf",)

    def _magic_matches(self, header: bytes) -> bool:
        return len(header) >= 8 and header[0] == 0xFF and b"BIOSEMI" in header[:8]

    def _open(self, assets: Sequence[Path]) -> mne.io.BaseRaw:
        return mne.io.read_raw_bdf(self.primary_path(assets), preload=False, verbose="ERROR")


class FIFAdapter(MNEAdapter):
    format_id = "fif"
    extensions = (".fif",)

    def _open(self, assets: Sequence[Path]) -> mne.io.BaseRaw:
        return mne.io.read_raw_fif(self.primary_path(assets), preload=False, verbose="ERROR")


class BrainVisionAdapter(MNEAdapter):
    format_id = "brainvision"
    extensions = (".vhdr",)

    def validate(self, assets: Sequence[Path], config: ImportConfig = None) -> None:
        paths = _safe_paths(assets)
        header = self.primary_path(paths)
        references = self._companion_names(header)
        names = {path.name.casefold() for path in paths}
        for reference in references:
            safe_name = Path(reference).name
            if safe_name != reference or Path(reference).is_absolute():
                raise ValidationError(
                    "unsafe_companion_reference",
                    "The BrainVision header contains an unsafe companion-file path.",
                )
            if safe_name.casefold() not in names:
                raise ValidationError(
                    "missing_companion",
                    f"The BrainVision header references {safe_name}, but that companion "
                    "file was not included. Upload the .vhdr, .vmrk, and .eeg files together.",
                    {"missing_file": safe_name},
                )
        super().validate(paths, config)

    def _companion_names(self, header: Path) -> tuple[str, ...]:
        text = header.read_text(encoding="utf-8-sig", errors="replace")
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str  # type: ignore[assignment]
        try:
            # BrainVision places a human-readable signature before its first INI
            # section; ConfigParser expects the input to begin with a section.
            section_start = text.find("[")
            if section_start < 0:
                raise configparser.MissingSectionHeaderError(str(header), 1, text[:200])
            parser.read_string(text[section_start:])
            common = parser["Common Infos"]
        except (configparser.Error, KeyError) as exc:
            raise ValidationError(
                "malformed_header", "The BrainVision header is malformed or lacks Common Infos."
            ) from exc
        references = [common.get("DataFile"), common.get("MarkerFile")]
        return tuple(
            reference for reference in references if reference and reference != "NO_MARKERS"
        )

    def _open(self, assets: Sequence[Path]) -> mne.io.BaseRaw:
        self.validate_companions_only(assets)
        return mne.io.read_raw_brainvision(
            self.primary_path(assets), preload=False, verbose="ERROR"
        )

    def validate_companions_only(self, assets: Sequence[Path]) -> None:
        paths = _safe_paths(assets)
        header = self.primary_path(paths)
        names = {path.name.casefold() for path in paths}
        for reference in self._companion_names(header):
            safe_name = Path(reference).name
            if safe_name != reference or Path(reference).is_absolute():
                raise ValidationError(
                    "unsafe_companion_reference",
                    "The BrainVision header contains an unsafe companion-file path.",
                )
            if safe_name.casefold() not in names:
                raise ValidationError(
                    "missing_companion",
                    f"The BrainVision header references {safe_name}, but that companion "
                    "file was not included. Upload the .vhdr, .vmrk, and .eeg files together.",
                    {"missing_file": safe_name},
                )


class EEGLABAdapter(MNEAdapter):
    format_id = "eeglab"
    extensions = (".set",)

    def validate(self, assets: Sequence[Path], config: ImportConfig = None) -> None:
        paths = _safe_paths(assets)
        primary = self.primary_path(paths)
        names = {path.name.casefold() for path in paths}
        content = primary.read_bytes()[: 2 * 1024 * 1024]
        for raw_reference in re.findall(rb"[A-Za-z0-9_. -]+\.fdt", content, flags=re.IGNORECASE):
            reference = raw_reference.decode("latin1", errors="ignore").strip("\x00 ")
            safe_name = Path(reference).name
            if safe_name and safe_name.casefold() not in names:
                raise ValidationError(
                    "missing_companion",
                    f"The EEGLAB dataset references {safe_name}, but that companion .fdt "
                    "file was not included.",
                    {"missing_file": safe_name},
                )
        super().validate(paths, config)

    def _open(self, assets: Sequence[Path]) -> mne.io.BaseRaw:
        return mne.io.read_raw_eeglab(self.primary_path(assets), preload=False, verbose="ERROR")
