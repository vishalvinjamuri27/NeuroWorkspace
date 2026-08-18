from __future__ import annotations

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from neuro_workspace.pyramid import choose_resolution, combine_envelope, envelope_from_raw


@given(
    st.lists(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=200,
    ),
    st.integers(min_value=2, max_value=12),
)
def test_envelopes_preserve_every_extreme(values: list[float], factor: int) -> None:
    raw = np.asarray([values], dtype=np.float32)
    envelope = envelope_from_raw(raw, factor)
    assert np.nanmin(envelope.minimum) == np.min(raw)
    assert np.nanmax(envelope.maximum) == np.max(raw)
    assert int(envelope.valid_count.sum()) == raw.size


def test_combined_envelopes_preserve_nan_semantics() -> None:
    raw = np.asarray([[np.nan, 2.0, -7.0, np.nan, 11.0]], dtype=np.float32)
    first = envelope_from_raw(raw, 2)
    second = combine_envelope(first, 2)
    assert float(np.nanmin(second.minimum)) == -7.0
    assert float(np.nanmax(second.maximum)) == 11.0
    assert int(second.valid_count.sum()) == 3


def test_resolution_is_bounded_for_large_windows() -> None:
    level, samples_per_bin = choose_resolution(10_000_000, 1200)
    assert level > 0
    assert (10_000_000 + samples_per_bin - 1) // samples_per_bin <= 1800
