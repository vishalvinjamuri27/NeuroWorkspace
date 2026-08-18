"""Transparent, non-diagnostic signal quality heuristics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.signal import welch


@dataclass(frozen=True, slots=True)
class QCThresholds:
    flat_std_si: float = 1e-8
    high_amplitude_si: float = 500e-6
    variance_outlier_z: float = 4.0
    dropout_run_seconds: float = 1.0
    discontinuity_diff_z: float = 12.0
    line_noise_ratio: float = 0.35
    line_frequency_hz: float = 60.0


@dataclass(frozen=True, slots=True)
class ChannelQC:
    channel_index: int
    finite_fraction: float
    standard_deviation_si: float | None
    maximum_absolute_si: float | None
    flat: bool
    high_amplitude: bool
    variance_outlier: bool
    dropout: bool
    discontinuity: bool
    line_noise_ratio: float | None
    excessive_line_noise: bool


def _longest_equal_run(values: NDArray[np.floating]) -> int:
    if values.size == 0:
        return 0
    finite = np.isfinite(values)
    equal = finite[1:] & finite[:-1] & (values[1:] == values[:-1])
    longest = current = 1
    for is_equal in equal:
        current = current + 1 if bool(is_equal) else 1
        longest = max(longest, current)
    return longest


def _line_noise(values: NDArray[np.float64], sfreq: float, line_hz: float) -> float | None:
    if values.size < 64 or sfreq <= line_hz * 2.2:
        return None
    frequencies, power = welch(values, fs=sfreq, nperseg=min(2048, values.size))
    valid = (frequencies >= 1.0) & (frequencies <= min(100.0, sfreq / 2))
    line = np.abs(frequencies - line_hz) <= 1.0
    total = float(np.trapezoid(power[valid], frequencies[valid]))
    line_power = float(np.trapezoid(power[line], frequencies[line]))
    return line_power / total if total > 0 else None


def compute_qc(
    values: NDArray[np.floating],
    sampling_frequency_hz: float,
    thresholds: QCThresholds | None = None,
) -> tuple[ChannelQC, ...]:
    """Compute review heuristics for a bounded channel-major signal window."""

    if values.ndim != 2:
        raise ValueError("values must have shape [channel, sample]")
    if sampling_frequency_hz <= 0:
        raise ValueError("sampling frequency must be positive")
    selected = thresholds or QCThresholds()
    standard_deviations = np.full(values.shape[0], np.nan, dtype=np.float64)
    for index, channel in enumerate(values):
        finite_values = channel[np.isfinite(channel)]
        if finite_values.size:
            standard_deviations[index] = np.std(finite_values, dtype=np.float64)
    finite_stds = standard_deviations[np.isfinite(standard_deviations)]
    median = float(np.median(finite_stds)) if finite_stds.size else np.nan
    mad = float(np.median(np.abs(finite_stds - median))) if finite_stds.size else np.nan

    results: list[ChannelQC] = []
    for index, channel in enumerate(values):
        finite_values = channel[np.isfinite(channel)].astype(np.float64)
        finite_fraction = finite_values.size / max(1, channel.size)
        std = standard_deviations[index]
        maximum = float(np.max(np.abs(finite_values))) if finite_values.size else None
        robust_z = 0.0 if not np.isfinite(mad) or mad == 0 else 0.6745 * (std - median) / mad
        diffs = np.diff(finite_values)
        diff_median = float(np.median(diffs)) if diffs.size else 0.0
        diff_mad = float(np.median(np.abs(diffs - diff_median))) if diffs.size else 0.0
        discontinuity = bool(
            diffs.size
            and diff_mad > 0
            and np.max(np.abs(diffs - diff_median)) / (1.4826 * diff_mad)
            > selected.discontinuity_diff_z
        )
        line_ratio = _line_noise(finite_values, sampling_frequency_hz, selected.line_frequency_hz)
        results.append(
            ChannelQC(
                channel_index=index,
                finite_fraction=finite_fraction,
                standard_deviation_si=float(std) if np.isfinite(std) else None,
                maximum_absolute_si=maximum,
                flat=bool(np.isfinite(std) and std <= selected.flat_std_si),
                high_amplitude=bool(maximum is not None and maximum >= selected.high_amplitude_si),
                variance_outlier=bool(abs(robust_z) >= selected.variance_outlier_z),
                dropout=_longest_equal_run(channel)
                >= max(2, int(selected.dropout_run_seconds * sampling_frequency_hz)),
                discontinuity=discontinuity,
                line_noise_ratio=line_ratio,
                excessive_line_noise=bool(
                    line_ratio is not None and line_ratio >= selected.line_noise_ratio
                ),
            )
        )
    return tuple(results)
