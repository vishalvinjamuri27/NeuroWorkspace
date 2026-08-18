from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from neuro_workspace.adapters.csv_adapter import CSVAdapter
from neuro_workspace.domain import CSVImportConfig
from neuro_workspace.errors import ValidationError


def config(**changes: object) -> CSVImportConfig:
    values: dict[str, object] = {
        "sampling_frequency_hz": 100.0,
        "time_column": None,
        "channel_columns": ("C3", "C4"),
        "channel_names": ("C3", "C4"),
        "units": ("uV", "mV"),
    }
    values.update(changes)
    return CSVImportConfig(**values)  # type: ignore[arg-type]


def test_csv_metadata_and_si_conversion(tmp_path: Path) -> None:
    path = tmp_path / "recording.csv"
    path.write_text("C3,C4\n1,2\n3,4\n5,6\n", encoding="utf-8")
    adapter = CSVAdapter()
    result = adapter.inspect_metadata([path], config())
    assert result.sample_count == 3
    assert result.channels[0].conversion_to_si == 1e-6
    values = np.concatenate(
        [chunk.values for chunk in adapter.iter_signal_chunks([path], 2, config())], axis=1
    )
    assert np.allclose(values[0], [1e-6, 3e-6, 5e-6])
    assert np.allclose(values[1], [2e-3, 4e-3, 6e-3])


def test_csv_rejects_irregular_timebase(tmp_path: Path) -> None:
    path = tmp_path / "recording.csv"
    path.write_text("t,C3,C4\n0,1,2\n0.01,3,4\n0.04,5,6\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="irregular"):
        CSVAdapter().inspect_metadata([path], config(sampling_frequency_hz=None, time_column="t"))
