"""Measure canonical ingestion and viewport latency on generated stress data."""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import psutil

from neuro_workspace.domain import ChannelMetadata, RecordingMetadata, SignalChunk
from neuro_workspace.ingestion import choose_chunk_samples
from neuro_workspace.signal_store import LocalZarrSignalStore


def generated_chunks(
    channels: int, samples: int, sampling_frequency_hz: float, chunk_samples: int
) -> Iterator[SignalChunk]:
    rng = np.random.default_rng(20260818)
    frequencies = np.linspace(7.0, 18.0, channels, dtype=np.float32)[:, None]
    for start in range(0, samples, chunk_samples):
        stop = min(start + chunk_samples, samples)
        timeline = np.arange(start, stop, dtype=np.float32)[None, :] / sampling_frequency_hz
        noise = rng.normal(0, 3e-6, size=(channels, stop - start)).astype(np.float32)
        values = 20e-6 * np.sin(2 * np.pi * frequencies * timeline) + noise
        if start <= samples // 2 < stop:
            values[0, samples // 2 - start] = 500e-6
        yield SignalChunk(start, values.astype(np.float32, copy=False))


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * quantile))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels", type=int, default=32)
    parser.add_argument("--duration-seconds", type=float, default=30 * 60)
    parser.add_argument("--sampling-frequency-hz", type=float, default=256)
    parser.add_argument("--queries", type=int, default=40)
    parser.add_argument(
        "--output-root", type=Path, default=Path(".cache/neuro-workspace/benchmarks")
    )
    args = parser.parse_args()
    if min(args.channels, args.duration_seconds, args.sampling_frequency_hz, args.queries) <= 0:
        parser.error("all numeric arguments must be positive")

    sample_count = int(args.duration_seconds * args.sampling_frequency_hz)
    version_id = f"stress-{uuid.uuid4().hex}"
    output_root = args.output_root.resolve()
    store = LocalZarrSignalStore(output_root)
    metadata = RecordingMetadata(
        format_id="generated-stress-v1",
        sampling_frequency_hz=args.sampling_frequency_hz,
        sample_count=sample_count,
        duration_seconds=args.duration_seconds,
        channels=tuple(
            ChannelMetadata(
                index=index,
                name=f"EEG {index + 1:03d}",
                source_name=f"EEG {index + 1:03d}",
                channel_type="eeg",
                unit="V",
                source_unit="V",
                source_sampling_frequency_hz=args.sampling_frequency_hz,
            )
            for index in range(args.channels)
        ),
    )
    chunk_samples = choose_chunk_samples(args.channels)
    process = psutil.Process()
    starting_rss = process.memory_info().rss
    peak_rss = [starting_rss]
    stop_monitor = threading.Event()

    def monitor_memory() -> None:
        while not stop_monitor.wait(0.01):
            peak_rss[0] = max(peak_rss[0], process.memory_info().rss)

    monitor = threading.Thread(target=monitor_memory, daemon=True)
    monitor.start()
    started = time.perf_counter()
    manifest = store.write(
        version_id,
        metadata,
        generated_chunks(args.channels, sample_count, args.sampling_frequency_hz, chunk_samples),
        source_hashes=(),
        software={"generator": "stress-v1"},
        chunk_samples=chunk_samples,
    )
    ingestion_seconds = time.perf_counter() - started
    stop_monitor.set()
    monitor.join()

    durations: list[float] = []
    for index in range(args.queries):
        query_start = (index / args.queries) * max(0, args.duration_seconds - 10)
        before = time.perf_counter()
        window = store.query(
            version_id,
            list(range(min(args.channels, 32))),
            query_start,
            min(10, args.duration_seconds),
            1200,
        )
        durations.append((time.perf_counter() - before) * 1000)
        if len(window.bin_start_seconds) > 1800:
            raise RuntimeError("viewport response exceeded the point budget")

    version_path = store.version_path(version_id)
    stored_bytes = sum(path.stat().st_size for path in version_path.rglob("*") if path.is_file())
    logical_raw_bytes = args.channels * sample_count * 4
    result = {
        "scenario": {
            "channels": args.channels,
            "duration_seconds": args.duration_seconds,
            "sampling_frequency_hz": args.sampling_frequency_hz,
            "samples_per_channel": sample_count,
        },
        "ingestion_seconds": round(ingestion_seconds, 3),
        "samples_per_second": round(args.channels * sample_count / ingestion_seconds),
        "peak_rss_delta_bytes": max(0, peak_rss[0] - starting_rss),
        "logical_raw_bytes": logical_raw_bytes,
        "canonical_stored_bytes": stored_bytes,
        "storage_ratio_to_float32_raw": round(stored_bytes / logical_raw_bytes, 4),
        "pyramid_levels": manifest.pyramid_levels,
        "waveform_queries": args.queries,
        "waveform_latency_ms_p50": round(statistics.median(durations), 3),
        "waveform_latency_ms_p95": round(percentile(durations, 0.95), 3),
        "version_path": str(version_path),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
