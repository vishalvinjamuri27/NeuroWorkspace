"""Generic recording adapter interface."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol

from neuro_workspace.domain import (
    AssetProbe,
    Detection,
    ImportConfig,
    RecordingMetadata,
    SignalChunk,
)


class RecordingAdapter(Protocol):
    format_id: str

    def detect(self, assets: Sequence[AssetProbe]) -> Detection | None: ...

    def validate(self, assets: Sequence[Path], config: ImportConfig = None) -> None: ...

    def inspect_metadata(
        self, assets: Sequence[Path], config: ImportConfig = None
    ) -> RecordingMetadata: ...

    def iter_signal_chunks(
        self,
        assets: Sequence[Path],
        chunk_samples: int,
        config: ImportConfig = None,
    ) -> Iterator[SignalChunk]: ...
