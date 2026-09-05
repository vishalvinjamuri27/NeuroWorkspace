# Engineering tour

A walk through NeuroWorkspace's hardest engineering problems, framed as the
questions a strong backend/systems interviewer would actually ask. Every
answer points at real source files and tests — this is meant to be checked,
not taken on faith. See the [README](../README.md) for the high-level
architecture and measured performance first.

## 1. How does an uploaded EEG file become a viewable recording?

The client requests a presigned URL and uploads bytes **directly to
S3-compatible object storage** — the API never proxies the file body. Calling
`POST /uploads/{id}/complete` creates a `Recording` row and a durable `Job`
row (`kind=INGEST`) in one database transaction, then the API *tries* to
dispatch the job to Celery — but the durable record of "this work needs to
happen" is the `Job` row, not the dispatch attempt. A Celery worker claims
the job with a single conditional `UPDATE` (`claim_job()`), parses the file
with the appropriate adapter (`packages/signal-core/.../adapters/`), writes
canonical Zarr arrays plus multi-resolution pyramids to object storage, and
creates an immutable `RecordingVersion` row pointing at that data. See
`apps/api/src/neuro_api/routes_uploads.py`'s `_dispatch_*` helpers and
`packages/signal-core/src/neuro_workspace/services/jobs.py::claim_job`.

## 2. How can an 8-hour recording be viewed without downloading it?

Signal data is stored as Zarr arrays chunked by `(channel, time-window)`,
with several pre-computed lower-resolution pyramid levels (min/max/valid-count
envelopes) alongside the raw data. A viewport request
(`GET /recordings/{id}/waveform`) picks the pyramid level matching the
requested pixel width, then reads only the object-storage chunks intersecting
the requested time/channel range via Zarr's `oindex` — never the full array.
This is why an identical 10-second request against a 30-second and a
30-minute recording measured the same ~7-8ms once warm (see
[PERFORMANCE.md](PERFORMANCE.md), "Viewport performance") — the cost is
architecturally bounded by the *viewport*, not the recording. Read
`packages/signal-core/src/neuro_workspace/signal_store.py::query()`.

## 3. How is processing lineage represented, and why not just mutate the recording in place?

Every transformation (currently: average-reference) reads one
`RecordingVersion`'s canonical data and writes a **new** `RecordingVersion`
row with `parent_version_id` pointing at its input — never overwriting the
original. This makes lineage a simple directed graph queryable straight from
Postgres (`ix_recording_versions_recording_created`), makes an immutable
version's cache key never need invalidation (only a TTL), and means a bug in
a processing operation can corrupt at most the *new* version, never the
input. See [ADR 0002](adr/0002-canonical-signal-storage.md) and
`neuro_workspace/db/models.py::RecordingVersion`.

## 4. What happens if two users edit the same annotation at the same time?

Both clients read the same `Annotation` row, each sees `revision=1`, both
`PATCH` with `revision=1`. The write is a conditional `UPDATE ... WHERE
revision = :revision`; one succeeds (revision becomes 2), the other's `WHERE`
clause matches zero rows and the API returns `409
annotation_revision_conflict` with the current server state — never a silent
overwrite. `BadChannel` review uses the identical pattern. This was verified
against two real concurrent HTTP clients, not just unit-tested — see
"Concurrent annotation update" in [RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md).
Code: `services/access.py`'s update paths; tests:
`tests/python/test_annotation_concurrency.py`.

## 5. What happens if Celery delivers the same task twice?

Nothing observable — the second delivery is a cheap no-op. `claim_job()` is a
single conditional `UPDATE jobs SET status='RUNNING', ... WHERE id=:id AND
(status='QUEUED' OR (status='RUNNING' AND lease_expires_at < now()))`. The
first delivery's claim matches and proceeds; the second delivery's identical
`UPDATE` matches zero rows (the job is already `RUNNING` with a live lease)
and the worker returns immediately. This was proven under genuine concurrent
Postgres connections, not just interleaved calls on one connection — 32,000
claim attempts across 16 real worker threads, zero double-claims (see
"Job-system database performance" in [PERFORMANCE.md](PERFORMANCE.md)) — and
live, by sending a real duplicate task message
(`RUNTIME_VALIDATION.md`, "Duplicate delivery").

## 6. What happens when a worker process dies mid-job?

The job's row carries a `lease_expires_at`, refreshed periodically while the
worker is alive (`LeaseHeartbeat`). If the worker is killed (SIGKILL, OOM,
node failure), the lease simply expires. `worker-beat`'s sweep
(`run_dispatch_sweep` → `reclaim_expired_leases`) finds jobs `RUNNING` past
their lease, atomically resets them to `QUEUED` (a compare-and-swap, not a
blind `UPDATE`, to prevent two overlapping sweeps from double-reclaiming the
same job — a real bug found and fixed during live testing, see
[RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md)'s "Worker crash mid-ingestion"),
and it gets redriven with no manual intervention, bounded by
`DEFAULT_LEASE_SECONDS`. Test:
`test_job_concurrency.py::test_concurrent_reclaim_sweeps_do_not_double_reclaim_the_same_job`.

## 7. Why does pagination stay fast at page 300 when it's slow with `OFFSET`?

Because the cursor is `(sort_column, id)`, not a row count. `OFFSET n`
degrades because Postgres has to scan and discard `n` rows every time, and it
also gives *wrong* results under concurrent inserts (a row inserted before
the current offset silently shifts every later page). Keyset pagination
anchors to the last row actually seen and asks the index for "the next N
rows after this key" — a plain index range scan regardless of depth. Measured
directly: walking 301 pages / 60,002 rows gave 2.55-40.30ms per page with **no
growth trend** (the first, misleading 7.6x-looking slowdown was a benchmark
harness bug — a reused session's identity map growing unboundedly, not real
request behavior; see "Cursor pagination at depth" in
[PERFORMANCE.md](PERFORMANCE.md) for the full story of catching that). Code:
`packages/signal-core/src/neuro_workspace/pagination.py`.

## 8. How is workspace (tenant) isolation enforced?

Every query that touches workspace-scoped data is routed through
`services/access.py`, which resolves the caller's `Identity` to active
workspace memberships and role capabilities before the query runs — there is
no code path that queries `recordings`/`annotations`/etc. by ID alone without
also filtering by the caller's membership. A recording that exists but
belongs to a workspace the caller isn't a member of returns the same `404` as
a recording that doesn't exist at all, so existence can't be probed. This was
verified live across two independent API replicas with two different signed-in
users (see "Stateless proof" in [RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md))
and is why the k6 load test in [PERFORMANCE.md](PERFORMANCE.md) had to
provision each virtual user its own workspace — a cross-tenant read attempt
is correctly refused, not a bug to route around. See
[ADR 0005](adr/0005-authentication-authorization.md).

## 9. What bottleneck did load testing actually find, and what didn't turn out to be a problem?

The database was never the bottleneck: Postgres CPU stayed under 3% at 40
concurrent virtual users. The bottleneck is that the shipped API image runs a
**single uvicorn process** — it measured at 257% CPU (2.5 cores) under load,
with tail latency growing roughly linearly with concurrency while throughput
barely moved (11.9 → 25.1 req/s across a 50x increase in VUs). Confirmed by
running the same test with `--workers 2`: CPU usage and throughput both
increased proportionally — direct evidence the fix is more server-side
process concurrency (more uvicorn workers, or more replicas — both already
supported), not a database or code change. What *didn't* turn out to be a
problem despite being checked directly: query plans at 8.4M rows (all
sub-5ms, no seq scans), pagination depth, cache-key correctness, and
job-claim correctness under real concurrent connections. See "Load test" and
"Bottlenecks discovered" in [PERFORMANCE.md](PERFORMANCE.md).

## 10. How does the application scale horizontally?

The API and worker are both stateless: no server-side session store (a
signed cookie carries identity), no replica-local cache of mutable state
(Redis is a shared cache, not per-replica), and no shared filesystem (signal
data is read/written directly against S3-compatible object storage by
whichever replica handles a request). This was proven, not just designed:
two independent API containers were brought up with no shared state, and a
session created against one was accepted by the other with identical
behavior (see "Phase 5 — Multiple replicas" in
[RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md)), and the same statelessness
was re-verified at 1/2/3 replicas on a real Kubernetes cluster (see
[KUBERNETES_VALIDATION.md](KUBERNETES_VALIDATION.md)). The one deliberately
non-scaled component is `worker-beat` (the outbox sweep) — it runs as a
single instance because its job (finding undispatched/lease-expired rows) is
naturally idempotent under overlap, so a second instance briefly running
during a rolling deploy just races harmlessly on the same atomic `UPDATE`s
everything else uses, rather than needing leader election.

## 11. Why Postgres + Redis + S3 instead of more specialized infrastructure?

Each was chosen for a property actually needed, not for its own sake:
Postgres because correctness under concurrency (unique constraints,
conditional updates) is cheap to get right there and metadata/lineage/jobs
are inherently relational; Redis because it's an adequate cache and a
message broker Celery already speaks, and nothing stored there is a system
of record; S3-compatible object storage because signal arrays are large,
chunk-accessed, and need to be reachable identically from any stateless
replica with no shared filesystem. A message queue beyond Redis (Kafka,
SQS), a dedicated cache layer beyond Redis, or a specialized time-series
database were all considered and rejected for lack of evidence they solve a
problem this stack actually has — see
[ADR 0001](adr/0001-monorepo-modular-monolith.md) and
[ADR 0004](adr/0004-job-queue.md) for the explicit alternatives sections.

## 12. What would need to change for substantially larger production scale?

Concretely, from measured evidence, not speculation:

- **API concurrency**: run multiple uvicorn workers per replica or more
  replicas — the current single-process image is the measured bottleneck at
  moderate concurrency (see question 9).
- **Connection pool ceiling vs. replica count**: each API process holds up to
  30 Postgres connections (`pool_size=10 + max_overflow=20`); against
  Postgres's default `max_connections=100`, that's roughly 3 replicas before
  headroom runs out. Scaling further needs a larger `max_connections`, a
  smaller per-replica pool, or a connection pooler (PgBouncer) in front of
  Postgres — none of which is implemented, since no benchmark here reached
  that many replicas. See "Connection pool behavior" in
  [PERFORMANCE.md](PERFORMANCE.md).
- **A production-grade cluster** (EKS/GKE/AKS) with the chart's non-dev path
  (externally-provisioned Postgres/Redis/S3) has never actually been
  exercised — only a local `kind` cluster has. See
  [KUBERNETES_VALIDATION.md](KUBERNETES_VALIDATION.md).
- **Queue-depth-based worker autoscaling** (a KEDA `ScaledObject` reading
  Celery/Redis queue length) would be a more accurate scaling signal than
  CPU for a job worker, but needs KEDA as a cluster dependency not yet
  validated here. See [KUBERNETES.md](KUBERNETES.md), "What's deliberately
  not here."
