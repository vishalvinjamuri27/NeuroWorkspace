"""Adapter selection with explicit ambiguity handling."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from neuro_workspace.adapters.base import RecordingAdapter
from neuro_workspace.adapters.csv_adapter import CSVAdapter
from neuro_workspace.adapters.mne_adapter import (
    BDFAdapter,
    BrainVisionAdapter,
    EDFAdapter,
    EEGLABAdapter,
    FIFAdapter,
)
from neuro_workspace.domain import AssetProbe
from neuro_workspace.errors import ValidationError


class AdapterRegistry:
    def __init__(self, adapters: Sequence[RecordingAdapter]) -> None:
        format_ids = [adapter.format_id for adapter in adapters]
        if len(format_ids) != len(set(format_ids)):
            raise ValueError("adapter format IDs must be unique")
        self._adapters = tuple(adapters)

    @property
    def format_ids(self) -> tuple[str, ...]:
        return tuple(adapter.format_id for adapter in self._adapters)

    def get(self, format_id: str) -> RecordingAdapter:
        for adapter in self._adapters:
            if adapter.format_id == format_id:
                return adapter
        raise ValidationError("unsupported_format", f"The format {format_id} is not supported.")

    def detect(self, assets: Sequence[AssetProbe]) -> RecordingAdapter:
        detections = [
            (detection, adapter)
            for adapter in self._adapters
            if (detection := adapter.detect(assets)) is not None
        ]
        if not detections:
            raise ValidationError(
                "unsupported_format",
                "The recording format could not be identified from its structure and files.",
            )
        detections.sort(key=lambda item: item[0].confidence, reverse=True)
        if len(detections) > 1 and detections[0][0].confidence == detections[1][0].confidence:
            raise ValidationError(
                "ambiguous_format",
                "The upload matches more than one format. Choose a format and review the files.",
                {"formats": [item[0].format_id for item in detections]},
            )
        return detections[0][1]


default_registry = AdapterRegistry(
    (
        cast(RecordingAdapter, EDFAdapter()),
        cast(RecordingAdapter, BDFAdapter()),
        cast(RecordingAdapter, BrainVisionAdapter()),
        cast(RecordingAdapter, EEGLABAdapter()),
        cast(RecordingAdapter, FIFAdapter()),
        cast(RecordingAdapter, CSVAdapter()),
    )
)
