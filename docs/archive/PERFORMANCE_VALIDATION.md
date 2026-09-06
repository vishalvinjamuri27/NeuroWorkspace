# Public-alpha performance validation

> **Superseded.** [PERFORMANCE.md](PERFORMANCE.md) is the current,
> comprehensive performance report (database, pagination, job concurrency,
> viewport, cache, load test, and object storage), measured against the
> S3-backed stack. This document is kept for history only — it measured the
> since-replaced `LocalZarrSignalStore` in isolation.

Measured 2026-08-18 on the release-preparation Mac with 24 GiB RAM, Python 3.13.5,
local APFS storage, generated float32 EEG-like signals, and the checked-in
`LocalZarrSignalStore`. Each case streamed deterministic chunks, built every
pyramid level, then issued 32-channel, 1,200-pixel, 10-second viewport queries.
These are product-oriented local measurements, not cloud capacity claims.

| Case | Logical raw | Canonical bytes | Ingest | Peak RSS increase | First request | Query p50 / p95 | Pan p50 / p95 | Zoom p50 / p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 32 ch × 30 min × 256 Hz | 58,982,400 B | 88,472,898 B | 2.097 s | 195,706,880 B | 36.184 ms | 36.813 / 42.114 ms | 37.206 / 38.426 ms | 37.265 / 49.313 ms |
| 64 ch × 2 hr × 512 Hz | 943,718,400 B | 1,420,454,502 B | 25.248 s | 300,400,640 B | 46.977 ms | 49.973 / 53.297 ms | 50.316 / 50.911 ms | 37.790 / 50.496 ms |
| 128 ch × 8 hr × 512 Hz | 7,549,747,200 B | 11,363,191,868 B | 210.100 s | 324,272,128 B | 48.093 ms | 48.146 / 59.876 ms | 52.112 / 53.525 ms | 40.947 / 54.884 ms |

All three specified cases completed. The largest built 12 pyramid levels at about
8.98 million channel-samples/second. Observed peak-RSS growth stayed under 325 MB
because ingestion is chunked. A 32-visible-channel response contained 1,280 bins
in the medium and large cases, showing that viewport cost remained bounded rather
than growing with full recording duration.

The “repeated same-window” store queries were 38.153 ms, 49.675 ms, and 54.061 ms
respectively. This layer does not have an application response cache, so OS/Zarr
cache effects are included but should not be labeled a guaranteed cached-service
latency. The web client memoizes identical waveform promises for the active
session; the expanded E2E flow exercised pan and zoom without an arbitrary wait.

## Browser observations

The attributed OpenNeuro sample was opened in the in-app Chromium browser, then
zoomed, panned, a channel was hidden, and the QC panel was opened. The UI remained
interactive during this manual pass, and the automated Chromium suite completed
all five flows in 10.9 seconds. The available in-app browser interface did not
expose reliable per-tab heap or dropped-frame counters, so **browser memory and
quantitative render smoothness are not claimed as measured**. The media capture
procedure launches Chromium with precise memory information so a deployment team
can add those counters when the target browser/hosting environment is selected.

## Interpretation

- Local signal-window latency is comfortably sub-second for all specified cases.
- The large ingest completed without OOM in this environment.
- Canonical storage is roughly 1.5× logical float32 raw because it stores the base
  signal plus extrema pyramids; storage planning must include source objects too.
- Worker wall time, network-volume I/O, object download, parser behavior, queue
  time, API serialization, and concurrent users are not represented here.
- Browser stability over hours and multiple open recordings remains an external
  alpha risk until measured on participant hardware.

Reproduce with `scripts/benchmark_signal_store.py` and the exact scenario flags
recorded in `USER_READY_ALPHA_REPORT.md`. Do not compare results across hardware
without recording the environment.
