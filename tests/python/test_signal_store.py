from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from neuro_workspace.domain import ChannelMetadata, RecordingMetadata, SignalChunk
from neuro_workspace.errors import ValidationError
from neuro_workspace.signal_store import LocalZarrSignalStore


def metadata(samples: int = 9) -> RecordingMetadata:
    return RecordingMetadata(
        format_id="test",
        sampling_frequency_hz=3.0,
        sample_count=samples,
        duration_seconds=samples / 3,
        channels=(
            ChannelMetadata(0, "C3", "C3", "eeg", "V"),
            ChannelMetadata(1, "C4", "C4", "eeg", "V"),
        ),
    )


def test_store_round_trip_and_extrema(tmp_path: Path) -> None:
    store = LocalZarrSignalStore(tmp_path / "signals")
    values = np.asarray(
        [[0, 1, 2, -9, 4, 5, 70, 7, 8], [9, 8, 7, 6, 5, 4, 3, 2, np.nan]],
        dtype=np.float32,
    )
    manifest = store.write("v1", metadata(), iter([SignalChunk(0, values)]), chunk_samples=4)
    assert manifest.pyramid_levels >= 1
    assert np.allclose(store.read_raw_window("v1", 0, 4), values[:, :4])
    window = store.query("v1", [1, 0], 0, 3, 2)
    assert min(value for value in window.channels[1].minimum if value is not None) == -9
    assert max(value for value in window.channels[1].maximum if value is not None) == 70
    assert window.channels[0].index == 1


def test_store_rejects_incomplete_and_unsafe_versions(tmp_path: Path) -> None:
    store = LocalZarrSignalStore(tmp_path / "signals")
    with pytest.raises(ValidationError, match="identifier"):
        store.version_path("../escape")
    with pytest.raises(ValidationError, match="every signal sample"):
        store.write(
            "short",
            metadata(),
            iter([SignalChunk(0, np.zeros((2, 2), dtype=np.float32))]),
        )
    assert not store.version_path("short").exists()
