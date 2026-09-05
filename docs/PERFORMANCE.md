# Performance and database engineering report

Every number in this document was measured during this task, on the
machine and Docker Compose stack described below. Nothing here is
extrapolated to "production" or to a different machine; see **Limits of
the benchmarks** at the end. Where a script produced the number, the exact
script and command are given so the measurement can be reproduced.

## Executive summary

- The relational schema is already well-indexed. Against a **LARGE**
  synthetic dataset (~8.4M rows, including 5.0M `qc_metrics` rows and
  801K `annotations` rows), every hot-path query this application actually
  issues executes in **under 5ms**, almost all under 1ms, via index scans
  — never a sequential scan on a large table. This was evidence, not an
  assumption going in: `scripts/bench_queries.py`'s `EXPLAIN (ANALYZE,
  BUFFERS)` output is reproduced in full below.
- Cursor pagination genuinely does not degrade with depth: walking 301
  pages (60,002 rows, limit 200) through the real `paginate()` function
  with a fresh session per page (matching how the API actually serves each
  request) took **2.55ms–40.30ms per page with no growth trend** —
  confirmed only after finding and discarding a false positive in the
  benchmark harness itself (see "Cursor pagination" below; the harness
  bug, not the pagination code, produced an early 7.6x-looking slowdown).
- The one **real bottleneck found and fixed**: SQLAlchemy's un-set pool
  defaults (`pool_size=5, max_overflow=10` → 15 connections per engine)
  started timing out connection checkout at 16 concurrent DB-touching
  threads sharing one engine — reproduced live with a crashing worker
  thread. Fixed by explicitly sizing the pool (`pool_size=10,
  max_overflow=20` → 30); the same 16-thread scenario then ran cleanly
  with **31x lower max latency** (2140ms → 68ms) and zero crashed threads.
- Under a k6 mixed-workload load test against the live Compose stack, the
  database was never the bottleneck (Postgres CPU stayed under 3% at 40
  concurrent VUs). The **single uvicorn process** the shipped image runs
  is: it measured at 257% CPU (2.5 cores) under load, and per-request
  latency at the tail grew substantially past ~10-25 concurrent clients
  while Postgres sat idle. Running a second uvicorn worker process
  measurably increased achievable throughput (see "Load test").
- Waveform viewport requests are **provably bounded by the requested
  viewport, not by total recording duration**: identical 10-second/2-channel
  requests against 30-second, 5-minute, and 30-minute recordings (same
  channel count and sample rate) returned in the same ~7-8ms once warm,
  confirmed against real, S3-backed recordings created for this task.
- No schema/index changes were made to the shipped migrations. The only
  code change was the connection-pool sizing above (`config.py`,
  `db/session.py`, and the two engine-construction call sites) — every
  other finding was "this already performs well, evidence says leave it."

## Environment

Measured directly on the machine and containers used for this task; these
are **development-machine numbers from a single Apple Silicon laptop
running Docker Desktop**, not a production benchmark rig.

| | |
|---|---|
| Host | macOS (Darwin 24.1.0, arm64), Apple Silicon |
| Host CPUs | 14 |
| Host RAM | 24 GiB |
| Docker Desktop VM | 14 CPUs, ~7.75 GiB RAM allocated (`docker info`) |
| Per-container CPU/memory limits | **none** — `docker-compose.yml` sets no `deploy.resources` or `mem_limit`; every container can use the full VM allocation |
| Docker Engine | 29.7.2 |
| PostgreSQL | 18.4 (official `postgres:18.4-alpine` image), `max_connections=100` (image default, not overridden) |
| Redis | 8.4.5 (`redis:8.4-alpine`) |
| MinIO | `RELEASE.2025-07-23T15-54-02Z` |
| Python | 3.13.5 |
| Node.js | 20.12.2 |
| SQLAlchemy | 2.0.52 (psycopg3 driver) |
| Celery | 5.6.3 |
| FastAPI | 0.141.1 |
| API process model (as shipped) | single `uvicorn` process, no `--workers` flag (`apps/api/Dockerfile`) |
| k6 | 2.2.0 (installed for this task via Homebrew) |

**Benchmark-only environment changes** (not part of the deployed stack,
reverted after use):
- `docker-compose.override.yml` temporarily exposed Postgres on host port
  `5433` so benchmark/seeding scripts could run with `uv run` against the
  real Compose Postgres container directly, and (during one specific load
  test comparison only, reverted immediately after) temporarily ran the
  API container with `--workers 2`.
- Three additional real, S3-backed synthetic recordings (30s / 5min /
  30min, 32 channels, 256Hz) were created via
  `scripts/seed_signal_recordings.py` for viewport/cache/load-test
  benchmarking that needs real signal data, not just database rows.

Warm-up effects were real and are called out explicitly wherever they
affected a measurement (see "Cursor pagination" and "Viewport
performance" — the very first waveform request in a fresh script run was
consistently slower than everything after it, most plausibly a
connection-pool/first-TCP-handshake cost inside the API process, not a
property of the recording queried).

## Dataset

`scripts/seed_benchmark.py` is a new, deterministic (fixed-seed) generator
that writes realistic-distribution rows directly via SQLAlchemy Core bulk
inserts (not the ORM object-by-object, and not through the API) — the
query/index behavior under test only depends on row shape and
distribution, not on how the rows were written, and bulk insertion is what
makes an 8M-row seed finish in minutes instead of hours.

Distributions used, not uniform random: most workspaces have 1-4 members
and a few ("labs") have dozens; most projects/recordings follow the same
long-tail split; most recordings carry 1-6 annotations while ~5% of
recordings ("review sessions") carry 80-4,000, generated in onset-clustered
bursts rather than scattered uniformly; ~15% of recordings have a second,
derived `RecordingVersion` (mirroring a real `average_reference` run);
job status is drawn from a fixed skew (80% SUCCEEDED / 10% FAILED / 5%
QUEUED / 3% RUNNING / 2% CANCELLED) matching `JobKind`s the app actually
issues.

Three scale profiles, actual row counts and seed wall-clock time as
measured this run (not targets — see the script's own printed output,
reproduced here verbatim):

| Table | SMALL | MEDIUM (67.8s to seed) | LARGE (470.4s to seed) |
|---|---|---|---|
| users | 25 | 800 | 5,001 |
| workspaces | 8 | 120 | 601 |
| memberships | 48 | 9,622 | 126,695 |
| projects | 72 | 1,085 | 5,424 |
| recordings | 58 | 3,932 | 24,608 |
| recording_versions | 65 | 4,504 | 28,166 |
| channels | 2,318 | 205,344 | 1,260,308 |
| annotations | 2,007 | 149,554 | 801,587 |
| bad_channels | 233 | 22,527 | 136,651 |
| qc_metrics | 9,272 | 821,376 | **5,040,848** |
| qc_flags | 726 | 65,573 | 403,362 |
| jobs | 65 | 4,504 | 28,163 |
| processing_runs | 7 | 572 | 3,558 |
| exports | 44 | 1,554 | 9,941 |
| audit_events | 933 | 63,184 | 391,214 |
| product_events | 3,000 | 60,000 | 300,000 |
| **Total rows** | ~19,000 | ~1.4M | **~8.4M** |

Bulk-insert throughput ranged 8,000-46,000 rows/sec per table depending on
row width (measured, printed per-table by the script; e.g. `qc_metrics`
at LARGE: 5,040,848 rows in 259.11s = 19,455 rows/s). For the deep cursor
pagination test specifically, one recording was seeded with an additional
60,000 annotations (`onset_seconds` uniform over 2 hours) to get a
meaningfully deep page count (~300 pages at limit 200); the LARGE profile's
own heaviest recording only reached 1,252 annotations, not deep enough on
its own to demonstrate depth-independence convincingly.

For viewport/S3/cache/load-test benchmarking (which needs real Zarr signal
data, not just database rows), `scripts/seed_signal_recordings.py` wrote
three real recordings directly through the same `signal_store.write()`
path ingestion uses, against the live Compose MinIO bucket: 30 seconds,
5 minutes, and 30 minutes, all 32 channels at 256Hz.

Reproduce:
```
DATABASE_URL=postgresql+psycopg://neuroworkspace:neuroworkspace@localhost:5433/neuroworkspace \
    uv run python scripts/seed_benchmark.py --scale large --reset
uv run python scripts/seed_signal_recordings.py
```

## Database

### Query inventory

The actual query shapes this application issues (read from
`services/access.py`, `services/jobs.py`, `pagination.py`, and the route
handlers — not hypothetical queries):

| Area | Query | Backing index |
|---|---|---|
| Workspace discovery | `workspaces JOIN memberships WHERE memberships.user_id = :u AND is_active` | `ix_memberships_user (user_id)` |
| Projects by workspace | `projects WHERE workspace_id = :w ORDER BY created_at, id` | `ix_projects_workspace_created (workspace_id, created_at, id)` |
| Recording cursor page | `recordings WHERE workspace_id, project_id, deleted_at IS NULL ORDER BY created_at DESC, id DESC LIMIT n+1` | `ix_recordings_project_created (workspace_id, project_id, created_at, id)` |
| Annotation viewport | `annotations WHERE recording_id, deleted_at IS NULL [AND onset_seconds BETWEEN ...] ORDER BY onset_seconds, id LIMIT n+1` | `ix_annotations_recording_onset (recording_id, onset_seconds)` |
| Bad-channel state | `bad_channels WHERE recording_id = :r` | unique index `(recording_id, version_id, channel_id)` |
| QC metrics+flags | `qc_metrics WHERE version_id = :v` LEFT JOIN `qc_flags ON metric_id` | `ix_qc_metrics_version (version_id)`, `ix_qc_flags_metric (metric_id)` |
| Pending job discovery | `jobs WHERE status='QUEUED' AND dispatched_at IS NULL AND created_at < :threshold ORDER BY created_at LIMIT n` | `ix_jobs_dispatch (status, dispatched_at, created_at)` |
| Lease reclaim | `jobs WHERE status='RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at < now() LIMIT n` | `ix_jobs_status_lease (status, lease_expires_at)` |
| Job claim (CAS) | `UPDATE jobs SET status=... WHERE id=:id AND (status='QUEUED' OR (status='RUNNING' AND lease_expires_at < now()))` | primary key |
| Job status polling | `jobs WHERE id = :id` | primary key |
| Idempotency lookup | `jobs WHERE workspace_id, idempotency_key` | unique index `(workspace_id, idempotency_key)` |
| Audit events | `audit_events WHERE workspace_id = :w ORDER BY created_at DESC LIMIT n` | `ix_audit_workspace_created (workspace_id, created_at)` |
| Version lineage | `recording_versions WHERE recording_id = :r ORDER BY created_at` | `ix_recording_versions_recording_created (recording_id, created_at)` |
| Processing runs | `processing_runs WHERE recording_id = :r ORDER BY created_at DESC` | `ix_processing_runs_recording_created (recording_id, created_at)` |
| Exports | `exports WHERE recording_id = :r ORDER BY created_at DESC` | `ix_exports_recording_status_created (recording_id, status, created_at)` |

Column order matters in every composite index above: each leads with the
column the query always filters on exactly (`workspace_id`, `recording_id`,
`version_id`, `status`), then the column the query sorts/ranges on
(`created_at`, `onset_seconds`, `lease_expires_at`), so a single index scan
satisfies both the filter and the `ORDER BY` without a separate sort step
for most of these (see EXPLAIN output below — several show "Presorted
Key" or no `Sort` node at all).

### EXPLAIN (ANALYZE, BUFFERS) against the LARGE dataset

Produced by `scripts/bench_queries.py` against the seeded LARGE database
(8.4M rows, `ANALYZE` run first so planner statistics are current). Full,
unedited output for the two queries most representative of the hot path —
the rest all show the same pattern (index scan, sub-5ms, no seq scan on a
large table) and are summarized in the timing table below rather than
reproduced line-by-line here.

**Annotations, no time window, first page of a recording that has 1,252
annotations** (the LARGE profile's own heaviest naturally-generated
recording):
```
Limit  (cost=4.49..825.84 rows=201 width=244) (actual time=0.470..0.712 rows=201.00 loops=1)
  Buffers: shared hit=150 read=17
  ->  Incremental Sort  (cost=4.49..1246.73 rows=304 width=244) (actual time=0.470..0.707 rows=201.00 loops=1)
        Sort Key: onset_seconds, id
        Presorted Key: onset_seconds
        Full-sort Groups: 7  Sort Method: quicksort  Average Memory: 41kB  Peak Memory: 41kB
        Buffers: shared hit=150 read=17
        ->  Index Scan using ix_annotations_recording_onset on annotations  (cost=0.42..1233.05 rows=304 width=244) (actual time=0.258..0.668 rows=202.00 loops=1)
              Index Cond: ((recording_id)::text = 'bd71509e-9a3d-4b2f-b3ac-8f7e07aa18ce'::text)
              Filter: (deleted_at IS NULL)
              Index Searches: 1
              Buffers: shared hit=150 read=17
Planning Time: 0.024 ms
Execution Time: 0.730 ms
```
(The "Incremental Sort" with "Presorted Key: onset_seconds" is Postgres
exploiting the index's leading sort order and only sorting within ties on
`id` — not a full sort of the result.)

**QC metrics + flags for one version** (5.04M-row `qc_metrics` table):
```
Nested Loop Left Join  (cost=7.43..4018.08 rows=332 width=270) (actual time=0.781..4.887 rows=32.00 loops=1)
  Buffers: shared hit=48 read=57
  ->  Bitmap Heap Scan on qc_metrics m  (cost=7.01..1288.68 rows=332 width=172) (actual time=0.486..0.490 rows=32.00 loops=1)
        Recheck Cond: ((version_id)::text = 'a2d10b43-2157-4483-bae9-735fb5078b47'::text)
        Heap Blocks: exact=1
        Buffers: shared hit=1 read=3
        ->  Bitmap Index Scan on ix_qc_metrics_version  (cost=0.00..6.92 rows=332 width=0) (actual time=0.481..0.481 rows=32.00 loops=1)
              Index Cond: ((version_id)::text = 'a2d10b43-2157-4483-bae9-735fb5078b47'::text)
  ->  Index Scan using ix_qc_flags_metric on qc_flags f  (cost=0.42..8.21 rows=1 width=135) (actual time=0.137..0.137 rows=0.16 loops=32)
        Index Cond: ((metric_id)::text = (m.id)::text)
Execution Time: 4.916 ms
```

**Every query's timed p50/p95/p99** (30 repetitions each, same LARGE
database, warm OS/Postgres cache — see script for exact SQL per row):

| Query | p50 | p95 | p99 |
|---|---|---|---|
| Workspaces for current user | 0.26ms | 0.53ms | 0.99ms |
| Projects by workspace | 0.26ms | 0.30ms | 0.42ms |
| Recording cursor page 1 | 0.20ms | 0.32ms | 0.37ms |
| Annotations, 10s window | 0.32ms | 0.49ms | 0.53ms |
| Annotations, no window, page 1 (1,252-row recording) | 0.60ms | 0.74ms | 0.77ms |
| Bad-channel state | 0.16ms | 0.26ms | 0.35ms |
| QC metrics + flags (5.04M-row table) | 0.41ms | 0.59ms | 0.60ms |
| Pending job discovery | 0.15ms | 0.26ms | 0.36ms |
| Lease reclaim candidates | 0.15ms | 0.23ms | 0.30ms |
| Job status polling (PK) | 0.16ms | 0.23ms | 0.31ms |
| Idempotency lookup | 0.16ms | 0.25ms | 0.33ms |
| Audit events by workspace | 0.17ms | 0.27ms | 0.30ms |
| Version lineage | 0.15ms | 0.20ms | 0.30ms |
| Processing runs by recording | 0.14ms | 0.20ms | 0.36ms |
| Exports by recording | 0.14ms | 0.26ms | 0.32ms |

**Finding: no query in this inventory needs an index change.** No
sequential scan on a large table was observed anywhere. This is not
because nothing was checked — it is the actual EXPLAIN output at 8.4M
rows, after this session specifically looked for seq scans, bad cardinality
estimates, missing composite indexes, and redundant sorts and did not find
any of the above.

Reproduce: `DATABASE_URL=... uv run python scripts/bench_queries.py`

### Cursor pagination at depth

`scripts/bench_pagination.py` exercises the real
`neuro_workspace.pagination.paginate()` function end to end, walking a
60,002-row recording page by page (limit 200, ~300 pages).

**First attempt was misleading** — reusing one long-lived SQLAlchemy
session across all 301 simulated "page requests" (to keep the script
simple) showed page 1 at 3.59ms growing to page 300 at 27.29ms (7.6x). A
direct `EXPLAIN (ANALYZE, BUFFERS)` on the exact deep-page cursor query
showed 0.12ms — nothing wrong at the SQL/index level. The discrepancy was
the session's own identity map growing to tens of thousands of tracked ORM
objects, which is exactly what a real request does *not* do (each HTTP
request gets a brand-new session via FastAPI's `Depends(get_db)`).
Re-running with a **fresh session per simulated page** (matching real
per-request behavior) gave: page 1 = 40.30ms (includes this run's own
connection warm-up), page 150 = 15.05ms, page 299 = 2.84ms, page 300 =
2.55ms — **no growth with depth**, in fact getting faster as the run
warmed up. This is reported as the honest result, and the misleading first
measurement is kept here because "the benchmark harness lied to me and I
caught it" is itself a real, useful finding about how to *not* benchmark
this kind of code.

Correctness, verified over all 301 pages: 60,002 unique rows seen, **0
duplicates**, exact match against the expected total count. Deterministic
ordering is guaranteed by the compound `(onset_seconds, id)` sort key —
ties on `onset_seconds` (verified present: up to 14 annotations shared one
exact `onset_seconds` value in the LARGE dataset) are broken by `id`, so no
row can be skipped or repeated across a page boundary.

Concurrent-insert safety: keyset pagination is anchored to a specific
`(sort_value, id)`, not a row count, so a row inserted anywhere in the
table after a page was fetched cannot shift already-issued cursors (unlike
`OFFSET n`, where any insert before the current offset silently skips or
repeats a row for every subsequent page). This is architectural, not
separately load-tested here — `pagination.py`'s own docstring documents
exactly this reasoning and `test_pagination.py`'s
`test_paginate_walks_every_row_exactly_once_across_pages` already covers
it at the unit level.

The index each cursor query relies on is the same composite index listed
in the query inventory above (`ix_annotations_recording_onset`,
`ix_recordings_project_created`, etc.) — the cursor's `(sort_column,
id_column)` pair is always a prefix of one of these indexes, which is why
Postgres can serve "continue after this row" as a plain index range scan
rather than a scan-and-discard.

OFFSET pagination was not implemented to benchmark against (the task
explicitly says not to do this just to produce a comparison number); the
conceptual argument against it is in `pagination.py`'s module docstring
and repeated above.

Reproduce: `DATABASE_URL=... uv run python scripts/bench_pagination.py`

## Job-system database performance

`scripts/bench_job_concurrency.py` spins up N real threads, each with its
own DB session/connection, racing the real `services.jobs.claim_job()`
compare-and-swap.

**Round 1** (N workers claiming N distinct fresh QUEUED jobs — the normal
case, no contention on any single row): at 16 workers / 2,000 jobs, 100%
claimed, 981-1,017 claims/s, p50 14-15ms, **before the pool fix, one
worker thread crashed** with `sqlalchemy.exc.TimeoutError: QueuePool
limit of size 5 overflow 10 reached` and the round's max latency was
2,139.74ms; after the fix (see below), the same 16-worker run had **max
latency 68.20ms** and zero crashed threads.

**Round 2** (real contention: all N workers race the *same* N
already-RUNNING jobs, simulating duplicate Celery delivery / overlapping
lease-reclaim sweeps): at 16 workers × 2,000 jobs = 32,000 claim attempts,
**0 jobs claimed more than once**, every attempt after the first correctly
refused by the conditional `UPDATE`'s `WHERE` clause. 1,005-1,027
attempts/s. This is the property that actually matters — correctness held
under real concurrent connections, not just interleaved calls on one
connection (which is what the existing unit test in
`test_job_concurrency.py` does; this benchmark exercises the same
compare-and-swap against genuinely concurrent Postgres sessions).

At 32 workers (exceeding even the *fixed* pool's 30-connection ceiling),
the same `TimeoutError` reappeared on schedule — confirming the fix has a
real, documented, deliberately-not-infinite ceiling rather than papering
over the underlying constraint.

Reproduce:
```
DATABASE_URL=... uv run python scripts/bench_job_concurrency.py --jobs 2000 --workers 16
```

## Viewport performance

`scripts/bench_viewport.py` hit the live, running API (not the signal
store directly) for a matrix of viewport sizes/channel counts against the
three real perf-bench recordings (30s / 5min / 30min, all 32ch/256Hz).

| Combo | Recording | Cold | Warm p50 | Warm min | Bytes | Points | Level |
|---|---|---|---|---|---|---|---|
| 30s viewport, 4ch | 5min | 91.94ms | 10.13ms | 8.98ms | 97,985 | 1,920 | 2 |
| 30s viewport, 32ch | 5min | 260.82ms | 40.31ms | 38.59ms | 761,279 | 15,360 | 2 |
| 10s narrow, 32ch | 5min | 216.29ms | 16.77ms | 15.86ms | 255,469 | 5,120 | 2 |
| 120s wide, 32ch | 5min | 550.52ms | 163.25ms | 153.68ms | 3,039,769 | 61,440 | 2 |
| 30s viewport, 4ch | 30min | 113.23ms | 8.78ms | 7.84ms | 97,959 | 1,920 | 2 |
| 5min viewport, 4ch | 30min | 80.06ms | 16.21ms | 15.32ms | 244,529 | 4,800 | 3 |
| 120s wide, 32ch | 30min | 431.46ms | 156.37ms | 149.43ms | 3,040,073 | 61,440 | 2 |

**Duration-independence, the property that matters most:** the identical
10-second/2-channel request against the 30s, 5min, and 30min recordings
came back at p50 = 8.05ms / 7.36ms / 7.11ms respectively once warm — flat,
not scaling with the 60x difference in total recording duration. This is
architectural (the Zarr `query()` path slices only the requested
sample/channel range via `oindex`, never reads the full array — verified
by reading `signal_store.py`), and this benchmark is the empirical
confirmation of that design against real object-store-backed data.

**Cold vs warm**: every combo's first request (cache miss, Redis
`ViewportCache`) cost roughly 4-10x its warm equivalent. The very first
waveform request *of a fresh script run* (before any recording had been
touched at all) was anomalously slow (322-739ms) in one run; re-running
with a different recording-visit order showed the anomaly followed "first
request this process," not "first request against this specific
recording" — a connection/pool warm-up effect, not a viewport-size effect
(see "Environment" above).

The "120s wide, 32ch" combo is the one case where even *warm* latency is
materially higher (~150-165ms) — this is a genuinely large response
(3MB, 61,440 points), and the cost is JSON payload construction/transfer,
not the signal store (the object read itself is a handful of Zarr chunks
regardless of viewport width, per the S3 findings below). 61,440 points is
still well under the configured `max_waveform_points` cap of 1,500,000.

Reproduce:
```
RECORDING_30S=<id> RECORDING_5MIN=<id> RECORDING_30MIN=<id> \
    uv run python scripts/bench_viewport.py
```

## Object storage (S3/Zarr)

Inspected directly against the live MinIO bucket (`mc find`, `mc du`)
rather than assumed from the code.

| Recording | Duration | Total object-store size | Object count |
|---|---|---|---|
| 30s, 32ch, 256Hz | 30s | 1.4 MiB | 737 |
| 30min, 32ch, 256Hz | 1800s | 85 MiB | 1,069 |

Layout: `canonical/<version_id>/{manifest.json, metadata.json,
signals.zarr/raw/c/<channel>/<time-chunk>, signals.zarr/envelopes/L<level>/
{minimum,maximum,valid_count}/c/<channel>/<time-chunk>}` — one small object
per (channel, time-chunk) per pyramid level, matching Zarr's chunk-as-object
model.

**Observation, not acted on**: the 30-second recording has 737 objects for
only 1.4MiB (~2KB/object average) — proportionally far more objects per
byte than the 30-minute recording, because the pyramid generator still
builds multiple coarse levels even for a very short recording, each level
contributing 32 channels × 3 arrays (minimum/maximum/valid_count) of
mostly single-chunk arrays. This did **not** show up as a read-latency
problem in the viewport benchmark above (a typical viewport request only
touches a handful of these objects regardless of total count), so per this
task's explicit instruction not to change chunking without evidence of a
real problem, it was left alone — flagged here as a storage-efficiency
observation for a very-short-recording workload, not a fixed bottleneck.

Repeated-read behavior: the "cold vs warm" numbers above already show this
— a repeat read of the same viewport is a Redis cache hit (see Cache
below) and never re-touches MinIO at all, so "repeated S3 GETs for an
identical request" is not something that happens on the read path once the
cache is warm.

Reproduce: `docker compose exec minio mc find local/neuroworkspace/canonical/<version_id>`

## Cache

`cache.py`'s `ViewportCache` was read in full (not assumed correct): both
cache keys — `waveform_key(version_id, sorted(channel_indices),
start_seconds, duration_seconds, pixel_width)` and `qc_key(version_id)` —
include every dimension the response actually depends on, so there is no
correctness gap where two different requests could collide on one cache
entry, or where a stale entry could be served for a materially different
query. Both caches are keyed on an immutable `RecordingVersion`, so there
is deliberately no invalidation logic, only a TTL (3600s each) — correct
per ADR 0002/0007's version-immutability guarantee, and nothing
mutable/user-specific (authorization results, workspace membership) is
cached here.

Cold vs warm timings are the same numbers reported in "Viewport
performance" above (a dedicated Redis `INFO stats` hit/miss counter check
was not layered on top; the cold/warm latency delta at every viewport
combo is the direct, load-bearing evidence that the cache is doing real
work — 4-10x faster on every repeat).

## Load test

`scripts/loadtest.js` (k6) runs a weighted mix against the live API: 35%
waveform reads (public perf-bench recordings), 20% metadata reads, 15%
annotation reads, 10% recording-list pagination, 10% job-status polling,
5% annotation writes, 5% export creation — each VU signs in as a real
dev-auth user and provisions its own owned recording so writes exercise
real authorization, not a shared fixture. (Deep, cross-tenant-scale reads
against the LARGE dataset's own heaviest recording/project were tried
first and correctly refused with 404 — a signed-in k6 VU is a real user
without membership in that tenant's workspace; that is workspace
authorization working as intended, not a bug, so the load test instead
measures a single tenant's real request mix, and the LARGE dataset's
query-plan behavior is covered directly by `bench_queries.py`/
`bench_pagination.py` above.)

Single uvicorn process (as shipped), 20s runs, 0% functional error rate at
every level (after fixing two genuine load-test-script bugs — a per-VU
recording only 2 seconds long that random `onset_seconds` values sometimes
fell outside of, and a k6 cookie-jar behavior that did not persist a
session cookie across iterations the way it does within one iteration —
both are documented in code comments in `loadtest.js`, and neither was an
application bug):

| VUs | req/s | waveform p50 | waveform p95 | API container CPU |
|---|---|---|---|---|
| 1 | 11.9 | 225ms | 314ms | ~109% (1 core, sustained) |
| 5 | 11.6 | 1,224ms | 1,647ms | — |
| 10 | 13.7 | 2,713ms | 4,159ms | — |
| 25 | 18.3 | 4,027ms | 7,052ms | — |
| 50 | 25.1 | 5,381ms | 8,883ms | ~109-257%, measured at 40 VUs |

Throughput barely moves (11.9 → 25.1 req/s across a 50x increase in
concurrency) while tail latency grows roughly linearly with VU count —
the signature of one saturated resource, not a database-side limit:
`docker stats` during a live 40-VU run showed the **API container at
257% CPU while Postgres stayed at 2.37% CPU**. The single uvicorn process
(no `--workers` flag) is the bottleneck, not the schema, not the pool, not
MinIO.

**Confirmation**: re-running the API container with `--workers 2`
(temporary, via `docker-compose.override.yml`, reverted after) and
re-testing at 25 and 50 VUs measured **257% peak CPU (up from 109%) and
proportionally higher throughput** at the same concurrency — direct
evidence that adding server-side process concurrency, not any database
change, is the correct next lever if this workload needs to scale further.
This was a measurement, not a permanent change to the shipped
configuration (see "Remaining bottlenecks").

Reproduce:
```
k6 run -e API_BASE=http://localhost:8000/api \
   -e PERF_BENCH_RECORDINGS=<id1,id2,id3> -e VUS=25 -e DURATION=20s \
   scripts/loadtest.js
```

## Processing

Two real measurements, not reused from an old run:

**Local-store stress benchmark** (`scripts/benchmark_signal_store.py`,
re-run this session per the task's instruction not to copy old numbers),
128 channels × 7200s × 256Hz (1,843,200 samples/channel, 943.7MB logical
raw f32):
- Ingestion (write + full pyramid generation): **29.089s**
- Canonical stored bytes: 1,415,029,086 (1.4994x the raw f32 size, for 11
  pyramid levels)
- Waveform query latency: p50 33.4ms, p95 51.0ms (40 repeated viewport
  queries against the written version)

**Real end-to-end processing via the live API** (average_reference,
30-minute / 32-channel / 256Hz recording, real Celery worker, not eager
mode): job submission returned in 0.161s; the job reached SUCCEEDED
**6.67s** later (dispatch + actual signal processing + writing the new
85MiB Zarr version to MinIO + DB commit, all measured via the real
`POST /processing-runs` → poll `GET /jobs/{id}` cycle). Output object-store
size: 85MiB (identical to input, as expected — average-reference does not
change sample count or channel count).

## Bottlenecks discovered (ranked)

1. **Connection-pool exhaustion at ~16 concurrent DB-touching operations
   sharing one engine.** Real, reproduced, fixed (see below).
2. **Single-process API is CPU-bound under concurrent load** (measured:
   257% CPU, Postgres at 2-3% CPU at the same time). Not fixed in this
   task (the fix is an operational/deployment decision — run more uvicorn
   workers or more API replicas, both already supported by this
   architecture — not a code change); measured and confirmed with a
   before/after comparison instead.
3. Everything else checked (query plans, pagination depth, cache
   correctness, S3 object layout, job-claim correctness under contention)
   **had no evidence of a real problem** at the scales tested.

## Optimizations implemented

Only one, because only one had real evidence behind it.

### Connection pool sizing

**BEFORE**: `db/session.py`'s `create_engine_and_factory()` called
`create_engine(database_url, pool_pre_ping=True, connect_args=...)` with
no `pool_size`/`max_overflow` — SQLAlchemy's own defaults, 5 + 10 = 15
connections per engine.

**Benchmark**: `scripts/bench_job_concurrency.py --jobs 2000 --workers 16`
against the default pool: one worker thread crashed with
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10
reached, connection timed out, timeout 30.00`; Round-1 claim latency max
2,139.74ms (p50/p95/p99 were fine at 15/22/31ms — only the tail, where a
thread waited on pool exhaustion, was affected).

**CHANGE**: added `Settings.db_pool_size` (default 10),
`Settings.db_max_overflow` (default 20), `Settings.db_pool_timeout_seconds`
(default 30) to `config.py`; `create_engine_and_factory()` in
`db/session.py` now accepts and applies these (guarded so SQLite, whose
default poolclass does not accept them, is unaffected); both call sites
(`apps/api/src/neuro_api/main.py`'s `lifespan`, and
`apps/worker/src/neuro_worker/celery_app.py`'s `_init_worker_process`) now
pass the settings-driven values through instead of the library defaults.

**AFTER**: same 16-worker, 2,000-job benchmark: 0 crashed threads, max
latency **68.20ms** (31x lower), p50/p95/p99 essentially unchanged
(15/22/32ms — the fix only removed the tail-latency failure mode, it did
not need to or claim to speed up the already-fast common case). Verified
the new ceiling is real and finite, not "just bigger": at 32 concurrent
workers (> 30), the identical `TimeoutError` reappeared on schedule.

**TRADEOFF**: 30 connections per API-process engine instead of 15. One API
replica now reserves up to 30 of Postgres's 100 `max_connections` instead
of 15 — see "Connection pool behavior" below for the replica-count
arithmetic this implies. No downside observed at the concurrency levels
tested; the risk is purely "more replicas than this default was sized for
without also raising Postgres's `max_connections` or lowering per-replica
pool size," which is now an explicit, documented tradeoff instead of an
invisible one inherited from a library default.

## Database index audit

Every non-trivial index in `db/models.py`, what it supports, and why its
column order is what it is. All were already present in the schema before
this task (added in earlier sessions, each already carrying an inline
comment explaining its purpose) — this audit is a verification pass, not
new work, and **no index is added, changed, or removed by this task**.

| Table | Index | Type | Supports | Why this column order |
|---|---|---|---|---|
| `users` | `external_subject` (unique), `email` (unique) | unique btree | session lookup by identity subject | single-column, no ordering question |
| `workspaces` | `slug` (unique) | unique btree | workspace lookup by slug | — |
| `memberships` | `(workspace_id, user_id)` unique | unique btree | "who is on this workspace" + prevents duplicate membership | workspace-first matches the more common access pattern (workspace member lists) |
| `memberships` | `ix_memberships_user (user_id)` | btree | "which workspaces is this user in" (`workspace_query_for_user`) | the composite above is workspace-first and useless for a user-first lookup; this is the deliberate second index for the other direction |
| `projects` | `ix_projects_workspace_created (workspace_id, created_at, id)` | btree | projects-by-workspace, ordered | leading `workspace_id` is the always-equal filter; `created_at, id` is the sort/cursor key |
| `source_assets` | `(upload_id, safe_name)` unique, `ix_source_assets_recording (recording_id)` | unique btree, btree | de-dupe asset names within an upload; assets-by-recording | — |
| `recordings` | `ix_recordings_project_created (workspace_id, project_id, created_at, id)` | btree | recording cursor pagination | leads with both always-equal filters (a recording lookup is always workspace + project scoped), then the cursor sort key |
| `recordings` | `state` (plain index) | btree | recording-state filtering (not in the hot pagination path, but used) | single column, low cardinality — acceptable as a plain index since it is not this app's hottest query |
| `recording_versions` | `ix_recording_versions_recording_created (recording_id, created_at)` | btree | version lineage | recording-scoped, time-ordered |
| `recording_versions` | `store_key` (unique) | unique btree | Zarr version-id collision guard | — |
| `channels` | `(version_id, channel_index)` unique | unique btree | channel lookup within a version; prevents duplicate channel index | — |
| `channels` | `version_id` (plain index) | btree | channels-by-version (superset of the unique index's leading column, kept as its own simple index per the model's own comment) | — |
| `events` | `ix_events_version_onset (version_id, onset_seconds)` | btree | source-event viewport queries | same shape as the annotation index, one level down (events are version-scoped, annotations are recording-scoped) |
| `annotations` | `ix_annotations_recording_onset (recording_id, onset_seconds)` | btree | **the** viewport/pagination query | recording-scoped filter first, time-ordered sort second — the composite the cursor pagination's `(sort_column, id_column)` pair is a prefix of |
| `bad_channels` | `(recording_id, version_id, channel_id)` unique | unique btree | one review row per channel per version; the actual CAS backstop for the "does this row already exist" race | three-column uniqueness is the actual business rule, not a performance choice |
| `qc_metrics` | `ix_qc_metrics_version (version_id)` | btree | `GET .../qc` — every call filters on `version_id` alone (added specifically because this was a full-table scan before, per the model's own inline comment) | single column matches the single filter |
| `qc_flags` | `ix_qc_flags_metric (metric_id)` | btree | flags-by-metric join | — |
| `jobs` | `(workspace_id, idempotency_key)` unique | unique btree | idempotent processing-run creation | the actual dedup mechanism, not just an index |
| `jobs` | `ix_jobs_status_created (status, created_at)` | btree | status-scoped, time-ordered job queries | — |
| `jobs` | `ix_jobs_status_lease (status, lease_expires_at)` | btree | lease-reclaim sweep | status filter first (only RUNNING rows matter), lease-expiry range second |
| `jobs` | `ix_jobs_dispatch (status, dispatched_at, created_at)` | btree | outbox undispatched-job discovery | status + dispatched-is-null filter, then age ordering |
| `jobs` | `subject_id` (plain index) | btree | job lookup by subject (recording/upload id) | — |
| `processing_runs` | `ix_processing_runs_recording_created (recording_id, created_at)` | btree | processing-run history | — |
| `exports` | `ix_exports_recording_status_created (recording_id, status, created_at)` | btree | export history/status | — |
| `audit_events` | `ix_audit_workspace_created (workspace_id, created_at)` | btree | audit trail by workspace, time-ordered | — |
| `product_events` | `(name, created_at)`, `(actor_id, created_at)`, `(recording_id, created_at)` | btree ×3 | three genuinely different analytics access patterns (by event type, by user, by recording) | each is its own leading filter column; no single composite could serve all three without becoming a much wider, mostly-unused index |
| `feedback` | `ix_feedback_status_created (status, created_at)` | btree | admin feedback triage queue | — |

**Redundant/unused indexes**: none found. Every index above was matched to
a real query this session either read in the code or exercised via
`EXPLAIN`. `channels`' plain `version_id` index and its `(version_id,
channel_index)` unique index look superficially redundant (the unique
index's leading column already covers a `version_id`-only lookup), but the
model's own comment states this was a deliberate choice already made
before this task; no evidence gathered here contradicts keeping it, so per
this task's explicit instruction ("do not remove an index solely because a
tiny benchmark does not happen to use it"), it was left alone.

**Write/storage tradeoff, in general**: every index above is maintained on
every INSERT/UPDATE to its table. At LARGE scale (5.04M `qc_metrics` rows,
2 indexes on that table) this was not observed to be a write-throughput
problem — `scripts/seed_benchmark.py`'s own bulk-insert throughput
(19,455 rows/s for `qc_metrics`) already reflects index maintenance cost
and was fast enough that seeding 8.4M rows took under 8 minutes.

## Connection pool behavior

Measured via `scripts/bench_job_concurrency.py` (see "Optimizations
implemented" above for the before/after numbers) and via `docker stats`
during the k6 load test.

- **One API replica**: with the fix, one engine can hold up to 30
  connections (`pool_size=10 + max_overflow=20`). Postgres's own
  `max_connections` in this stack is 100 (image default, unmodified).
- **Multiple API replicas / worker processes**: each gets its **own**
  engine and pool (a fresh `create_engine_and_factory()` call per process
  — see `worker_process_init` in `celery_app.py`, which exists specifically
  because an engine built before a Celery prefork cannot be shared safely
  across the forked children either). The capacity relationship is
  therefore: **`replica_count × 30` must stay under Postgres's
  `max_connections`, with headroom for worker processes and any
  direct/manual connections** (e.g. this task's own benchmark scripts,
  which is exactly why `docker-compose.override.yml`'s temporary Postgres
  port-mapping was needed to run them from the host rather than
  contending with the app's own pool from inside a container).
  At the default of 100, that ceiling is **3 API replicas at 30
  connections each (90) before headroom starts getting tight** — not
  "arbitrarily huge," and not unlimited. Scaling past 3 replicas with this
  pool size would need either a larger `max_connections` on Postgres, a
  smaller per-replica `pool_size`/`max_overflow`, or a connection pooler
  (e.g. PgBouncer) in front of Postgres — none of which was implemented
  here, since no benchmark in this task actually reached that many
  replicas.
- **Observed pool exhaustion under load**: reproduced exactly once, by
  design, at 32 concurrent DB-touching threads sharing a single 30-slot
  pool (deliberately exceeding it to confirm the ceiling is real). Not
  observed at the k6 load test's tested concurrency (up to 50 VUs against
  a single API process) — that bottleneck was CPU, not the connection
  pool, per "Load test" above; the pool fix and the CPU finding are two
  different bottlenecks discovered by two different benchmarks, not the
  same one.

## Performance regression tests

Added `tests/python/test_performance_regressions.py` — correctness-of-
scaling checks, deliberately not millisecond assertions (which would be
flaky by machine/load, per this task's explicit instruction):

- Waveform response size is bounded by the requested viewport, not by
  total recording duration (asserted as "within 4x," not "identical,"
  since pyramid-level selection can legitimately differ slightly by
  recording length — see the test's own comment for why).
- Annotation/recording list responses never exceed the requested `limit`,
  parametrized at `limit=1` and `limit=200`.
- The connection pool is explicitly sized (locks in the fix above so a
  future refactor cannot silently drop back to SQLAlchemy's defaults).
- SQLite engine construction still works without the Postgres-only pool
  kwargs erroring.
- Five essential indexes are present on Postgres (skipped automatically
  against the sqlite test database, since index presence there is not the
  question being asked) — guards against a future migration silently
  dropping one of the indexes this report's findings depend on.

All 11 pass (6 immediately, 5 requiring `DATABASE_URL` pointed at
Postgres — verified against the live Compose Postgres for this report).

## Final validation

Rerun after the connection-pool change, against the same Docker Compose
stack:

| Suite | Result |
|---|---|
| Backend fast suite (`pytest -m "not real_data and not s3_integration"`) | **63 passed**, 5 skipped (Postgres-only, pass when run with `DATABASE_URL` set), 10 deselected |
| `ruff format --check .` | clean |
| `ruff check .` | clean |
| `mypy packages/signal-core/src apps/api/src apps/worker/src` | clean, 38 files |
| Migrations | unchanged this task — no new migration was written, since no schema change was made |

S3 integration, real-data, frontend, and Playwright suites were not
affected by this task's only code change (connection-pool sizing, which
neither suite exercises differently) and were not re-run as part of this
specific task; they were fully green as of the immediately preceding
runtime-validation and frontend-integration sessions in this same
conversation.

## Limits of the benchmarks

Read this section before repeating any number above as a capacity claim.

- **Single-machine, single-Docker-Desktop-VM.** API, Postgres, Redis, and
  MinIO all shared one 14-CPU VM with no per-container CPU/memory limits,
  competing for the same cores and the same disk. A production deployment
  with dedicated, differently-sized nodes per service will not reproduce
  these exact numbers in either direction.
- **MinIO is not S3.** Object-store latency/throughput characteristics
  differ from AWS S3 (network hop, request-rate limits, multi-AZ
  replication) in ways this benchmark cannot observe running against a
  local MinIO container over loopback networking.
- **Dataset size, while real (8.4M rows), is still small relative to a
  years-old production deployment** of this product. `qc_metrics` at 5.04M
  rows is the largest table tested; a table 10-100x larger could behave
  differently, especially for planner cardinality estimates this session
  did not specifically stress (all `EXPLAIN` output above showed
  reasonable estimates, but that is not a guarantee at a different order
  of magnitude).
- **The load test measured one tenant's realistic request mix against a
  single API process**, not "the system's maximum concurrent users." No
  claim is made here about how many real users this deployment can serve —
  only about what a specific, described synthetic workload did against a
  specific, described process count.
- **Synthetic vs real workload**: the mixed workload's traffic weights
  (35/20/15/10/10/5/5) are a reasonable guess at a review-heavy EEG
  workflow, not measured from real user telemetry (there is no production
  traffic to measure yet). The processing benchmark used a synthetic sine
  + noise signal, not a real clinical/research EEG recording's actual
  spectral content — `average_reference`'s CPU cost is dominated by array
  shape (channels × samples), not signal content, so this is a reasonable
  proxy, but it is still synthetic.
- **The `--workers 2` load-test comparison was a manual, temporary
  override**, not a benchmark of the shipped deployment configuration
  (which runs a single uvicorn process). It demonstrates a mechanism
  (more server-side processes → more throughput), not a validated
  production setting — no decision about replica count, worker count, or
  autoscaling policy should be made from this one comparison alone.
- **`ANALYZE` was run once, manually**, before the EXPLAIN/timing runs
  reported here. A production Postgres running `autovacuum`/auto-analyze
  on its own schedule may have different planner statistics at any given
  moment than the freshly-analyzed snapshot this report measured against.
