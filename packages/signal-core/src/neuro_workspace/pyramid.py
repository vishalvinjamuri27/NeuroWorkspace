"""Extrema-preserving multiresolution primitives."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class Envelope:
    """Min/max envelope for `[channel, bin]` with finite-value counts."""

    minimum: NDArray[np.float32]
    maximum: NDArray[np.float32]
    valid_count: NDArray[np.int32]


def envelope_from_raw(values: NDArray[np.floating], factor: int = 4) -> Envelope:
    """Reduce channel-major values into exact finite min/max bins."""

    if values.ndim != 2:
        raise ValueError("values must have shape [channel, sample]")
    if factor < 2:
        raise ValueError("factor must be at least 2")
    channel_count, sample_count = values.shape
    bin_count = (sample_count + factor - 1) // factor
    minimum = np.full((channel_count, bin_count), np.nan, dtype=np.float32)
    maximum = np.full((channel_count, bin_count), np.nan, dtype=np.float32)
    valid_count = np.zeros((channel_count, bin_count), dtype=np.int32)

    for bin_index in range(bin_count):
        window = values[:, bin_index * factor : min((bin_index + 1) * factor, sample_count)]
        finite = np.isfinite(window)
        counts = finite.sum(axis=1, dtype=np.int32)
        valid_count[:, bin_index] = counts
        safe_min = np.where(finite, window, np.inf).min(axis=1)
        safe_max = np.where(finite, window, -np.inf).max(axis=1)
        has_value = counts > 0
        minimum[has_value, bin_index] = safe_min[has_value].astype(np.float32)
        maximum[has_value, bin_index] = safe_max[has_value].astype(np.float32)
    return Envelope(minimum=minimum, maximum=maximum, valid_count=valid_count)


def combine_envelope(previous: Envelope, factor: int = 4) -> Envelope:
    """Combine adjacent envelope bins while preserving every finite extremum."""

    if previous.minimum.shape != previous.maximum.shape:
        raise ValueError("minimum and maximum shapes differ")
    if previous.valid_count.shape != previous.minimum.shape:
        raise ValueError("valid_count shape differs")
    if factor < 2:
        raise ValueError("factor must be at least 2")

    channel_count, source_bins = previous.minimum.shape
    target_bins = (source_bins + factor - 1) // factor
    minimum = np.full((channel_count, target_bins), np.nan, dtype=np.float32)
    maximum = np.full((channel_count, target_bins), np.nan, dtype=np.float32)
    valid_count = np.zeros((channel_count, target_bins), dtype=np.int32)
    for bin_index in range(target_bins):
        part = slice(bin_index * factor, min((bin_index + 1) * factor, source_bins))
        counts = previous.valid_count[:, part].sum(axis=1, dtype=np.int32)
        valid_count[:, bin_index] = counts
        has_value = counts > 0
        if np.any(has_value):
            with np.errstate(all="ignore"):
                part_min = np.nanmin(previous.minimum[:, part], axis=1)
                part_max = np.nanmax(previous.maximum[:, part], axis=1)
            minimum[has_value, bin_index] = part_min[has_value]
            maximum[has_value, bin_index] = part_max[has_value]
    return Envelope(minimum=minimum, maximum=maximum, valid_count=valid_count)


def choose_resolution(sample_count: int, pixel_width: int, factor: int = 4) -> tuple[int, int]:
    """Return `(level, samples_per_bin)` with at most 1.5 bins per pixel."""

    if sample_count < 0 or pixel_width <= 0:
        raise ValueError("sample_count must be nonnegative and pixel_width positive")
    target = max(1, int(pixel_width * 1.5))
    level = 0
    samples_per_bin = 1
    while (sample_count + samples_per_bin - 1) // samples_per_bin > target:
        level += 1
        samples_per_bin *= factor
    return level, samples_per_bin
