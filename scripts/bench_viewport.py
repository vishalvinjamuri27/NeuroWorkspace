"""Waveform viewport + cache benchmark for docs/PERFORMANCE.md.

Hits the real, running API (not the signal store directly) for a matrix of
viewport sizes/channel counts against the three perf-bench recordings from
scripts/seed_signal_recordings.py, and reports:

  - cold (first request) vs warm (repeat, cache hit) latency
  - response payload bytes and point count
  - whether request duration scales with viewport size or with total
    recording duration (the key architectural property under test)

    RECORDING_30S=<id> RECORDING_5MIN=<id> RECORDING_30MIN=<id> \\
        uv run python scripts/bench_viewport.py
"""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.request

API_BASE = os.environ.get("API_BASE", "http://localhost:8000/api")

RECORDINGS = {
    "30s": os.environ.get("RECORDING_30S"),
    "5min": os.environ.get("RECORDING_5MIN"),
    "30min": os.environ.get("RECORDING_30MIN"),
}


def fetch(path: str) -> tuple[float, int, dict]:
    start = time.perf_counter()
    with urllib.request.urlopen(f"{API_BASE}{path}") as resp:
        body = resp.read()
        etag = resp.headers.get("ETag")
    elapsed = (time.perf_counter() - start) * 1000
    return elapsed, len(body), {"etag": etag, "payload": json.loads(body)}


def point_count(payload: dict) -> int:
    channels = payload.get("channels", [])
    if not channels:
        return 0
    return len(channels) * len(channels[0].get("minimum", []))


def main() -> None:
    combos = [
        # (label, start_seconds, duration_seconds, pixel_width, channel_count)
        ("30s viewport, 4ch", 0, 30, 800, 4),
        ("30s viewport, 32ch", 0, 30, 800, 32),
        ("5min viewport, 4ch", 60, 300, 1200, 4),
        ("30min viewport, 4ch", 300, 1800, 1200, 4),
        ("10s narrow viewport, 32ch", 5, 10, 400, 32),
        ("120s wide viewport, 32ch", 100, 120, 1600, 32),
    ]

    print("Recordings under test:", RECORDINGS)
    print()

    for recording_label, recording_id in RECORDINGS.items():
        if not recording_id:
            print(f"SKIPPED {recording_label}: no id provided")
            continue
        total_duration = {"30s": 30, "5min": 300, "30min": 1800}[recording_label]
        print(f"=== recording={recording_label} (duration={total_duration}s) id={recording_id} ===")
        for combo_label, start, duration, pixel_width, channels in combos:
            if start + duration > total_duration + 1:
                continue
            channel_params = "&".join(f"channel_index={i}" for i in range(channels))
            path = (
                f"/recordings/{recording_id}/waveform"
                f"?start_seconds={start}&duration_seconds={duration}"
                f"&pixel_width={pixel_width}&{channel_params}"
            )
            cold_elapsed, cold_bytes, cold = fetch(path)
            warm_samples = []
            for _ in range(10):
                elapsed, _nbytes, _result = fetch(path)
                warm_samples.append(elapsed)
            warm_samples.sort()
            points = point_count(cold["payload"])
            print(
                f"  {combo_label:<28} cold={cold_elapsed:7.2f}ms  "
                f"warm_p50={statistics.median(warm_samples):6.2f}ms  "
                f"warm_min={min(warm_samples):6.2f}ms  "
                f"bytes={cold_bytes:>8,}  points={points:>7,}  "
                f"level={cold['payload'].get('level')}"
            )
        print()

    print("=== Duration-scaling check: does a wider/longer recording change per-viewport cost? ===")
    for recording_label, recording_id in RECORDINGS.items():
        if not recording_id:
            continue
        path = (
            f"/recordings/{recording_id}/waveform?start_seconds=0&duration_seconds=10"
            "&pixel_width=800&channel_index=0&channel_index=1"
        )
        samples = [fetch(path)[0] for _ in range(15)]
        samples.sort()
        print(
            f"  identical 10s/2ch viewport on {recording_label:>5} recording: "
            f"p50={statistics.median(samples):.2f}ms min={min(samples):.2f}ms"
        )


if __name__ == "__main__":
    main()
