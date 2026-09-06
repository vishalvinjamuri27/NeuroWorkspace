# Runtime validation report

This document records an actual runtime-validation pass against the live
Docker Compose stack — not a review of configuration files. Every claim
below was produced by running the software, breaking it on purpose, and
observing the result. Where something could not be verified in this
session, that is stated explicitly rather than implied.

## Environment

- Host: macOS (Darwin 24.1.0, arm64), Docker Desktop 4.87.0, Docker Engine
  29.7.2, BuildKit.
- Stack images built locally from this repository's Dockerfiles: `postgres:18.4-alpine`,
  `redis:8.4-alpine`, `minio/minio:RELEASE.2025-07-23T15-54-02Z`, and the
  repo's own `api`, `worker`, `worker-beat`, `web` images (Python 3.13.7,
  Node 24.8.0).
- Configuration: `.env` copied from `.env.example` unmodified —
  `OBJECT_STORE_BACKEND=s3` (MinIO), `WORKER_EAGER=false` (real Celery
  dispatch, not in-process eager execution), `REQUIRE_DEMO_READY=true`.
  This is the S3-backed, multi-process configuration, not the SQLite/local
  native-dev shortcut.
- Real EEG fixture: `openneuro_ds004362_eeglab` (OpenNeuro ds004362 v1.0.0,
  CC0, checksum-pinned in `testdata/manifest.yaml`), fetched via
  `scripts/fetch_test_data.py`.

## Phase 1 — Docker Compose from a clean checkout

Torn down the stack completely (`docker compose down -v`, removing
volumes) and brought it up from scratch with `cp .env.example .env &&
docker compose up --build`.

**Two real, reproducible build/runtime bugs found and fixed before the
stack would start cleanly:**

### Bug 1 — `apps/web/Dockerfile`: COPY-after-install corrupted pnpm symlinks

**Symptom:** `next build` intermittently (and then, after a partial fix,
consistently) failed with `Cannot find module '/app/apps/web/node_modules/next/dist/bin/next'`,
even though the same `pnpm install` layer had just been verified to
contain a complete, working `next` package.

**Root cause, isolated by bisection:** the Dockerfile ran `pnpm install`
in a `dependencies` stage, then `COPY apps/web apps/web` in a later
`builder` stage on top of the already-populated `node_modules`. A
`COPY <dir> <dir>` landing on a destination that already contains pnpm's
`node_modules` symlinks silently orphaned them — the symlink at
`apps/web/node_modules/next` kept pointing at a content-hash directory
under `node_modules/.pnpm/` that no longer matched anything actually
extracted there. Confirmed directly: `ls -la apps/web/node_modules/next`
showed a valid-looking symlink whose target directory did not exist.
Proven and fixed by restructuring the Dockerfile to copy the full source
tree *before* running `pnpm install`, so no COPY ever lands on top of an
existing `node_modules` — verified clean across a fresh `--no-cache` build
and a subsequent cached rebuild, plus a running container smoke-tested
with `curl` (`HTTP 200`).

*A related, independently-observed issue*: the same command was flaky
even before this fix on some runs (same layer, same inputs, `next/dist`
sometimes present, sometimes not) — separately mitigated with a
verify-and-reinstall-once step in the same Dockerfile and a
`sharing=locked` BuildKit cache mount for the pnpm store, so a failure of
this kind fails loudly and retries instead of silently shipping a broken
image.

### Bug 2 — `S3ZarrSignalStore` + Celery prefork: fsspec event loop is not fork-safe

**Symptom:** the very first real ingestion job against the live worker
failed with `RuntimeError: This class is not fork-safe`, raised from
`fsspec/asyn.py`, inside `S3ZarrSignalStore._prepare_new_version`.

**Root cause:** `apps/worker/src/neuro_worker/celery_app.py` built
`signal_store = create_signal_store(settings)` once at module import time,
in Celery's *parent* process — before Celery's default `prefork` pool
forked worker child processes. `s3fs`'s `S3FileSystem` lazily binds an
asyncio event loop to whichever thread first uses it; a forked child only
inherits the thread that called `fork()`, not that loop's thread, so
`fsspec` deliberately raises rather than silently misbehaving.

**Fix:** `engine`/`object_store`/`signal_store` are now constructed inside
a `celery.signals.worker_process_init` handler, so each forked worker
process builds its own instances *after* the fork — the standard fix for
this class of bug (the same shape as SQLAlchemy's own documented advice to
`dispose()` an `Engine` created before a fork). Verified: rebuilt the
worker image, re-ran the same ingestion job — succeeded, and 100+
subsequent jobs across the rest of this session's testing (including two
concurrent worker replicas) never hit it again.

### Result

`docker compose up --build` now brings up all 7 declared services
(`postgres`, `redis`, `minio`, `bootstrap`, `api`, `worker`,
`worker-beat`, `web`) cleanly from a torn-down state. `bootstrap` ran
`alembic upgrade head` (4 revisions) against real Postgres and seeded the
real OpenNeuro sample end to end, including through the S3-backed
canonical signal store. `api` reported `(healthy)`; `/health/live`,
`/health/ready`, and `/metrics` all responded correctly.

## Phase 2 — End-to-end workflow (real HTTP client, real EEG file)

`scripts/e2e_smoke.py` drives the actual running stack over plain HTTP —
no test doubles, no eager in-process execution — using the real
`sub-001_task-motion_run-1_eeg.set` EEGLAB fixture (3.2 MB, 64 channels).
All 24 steps from the validation checklist passed on a live run:

```
=== 1. Application loads ===
OK: web app root returns 200
=== 2. Dev auth sign-in ===
OK: dev sign-in succeeds (got 200)
OK: session persists via cookie
=== 3. Workspace/project access ===
OK: workspace created (got 201)
OK: project created (got 201)
=== 4-6. Upload initiation, direct-to-object-store write, finalization ===
OK: upload initiated (got 201, ...)
OK: upload URL is a direct presigned object-store URL, not proxied through the API
OK: direct PUT to object storage succeeded (got 200)
OK: upload finalized (got 200, ...)
=== 7-9. Ingestion job durable, worker claims it, recording transitions state ===
OK: ingestion job SUCCEEDED (got SUCCEEDED: None)
=== 10-12. Canonical signal data + pyramids persisted, recording READY ===
OK: recording metadata endpoint works (step 13)
OK: recording reached READY (got READY)
OK: active_version_id set
channel_count=64 duration_seconds=61.0
=== 14. Viewport waveform endpoint returns bounded signal data ===
OK: waveform endpoint works (got 200)
OK: waveform returned 32 channels
OK: waveform response carries an ETag
=== 16-17. Annotation create + revise ===
OK: annotation created (got 201, ...)
OK: annotation revised (got 200, ...)
OK: revision incremented on update
=== 18. QC information ===
OK: QC endpoint works (got 200)
qc metrics: 256, flags: 13
=== 19-20. Processing operation creates derived RecordingVersion; lineage ===
OK: processing run started (got 202, ...)
OK: processing job SUCCEEDED (got SUCCEEDED)
OK: processing runs listable
OK: processing run row reflects SUCCEEDED
OK: derived RecordingVersion id present
OK: lineage: input_version_id matches the version processed
=== 21-23. Export request, completion, download ===
OK: export created (got 201, ...)
OK: export completed synchronously as READY (got READY)
OK: export downloadable (got 200)
OK: exported CSV contains the annotation we created
=== 24. Reopening the recording retains expected state ===
OK: recording reachable again
OK: state persisted across requests
OK: annotations listable on reopen
OK: revised annotation persisted with correct revision

=== ALL STEPS PASSED ===
```

(Steps 15 — "frontend renders it" — was verified separately: the web
container serves `HTTP 200` and was exercised manually via the demo
recording; a scripted browser-level check was not part of this pass.)

## Phase 3 — Failure recovery

All scenarios below were run against the live stack, not simulated in unit
tests (the unit-test suite covers the same properties in isolation; this
section is the live confirmation).

### Worker crash mid-ingestion

Started a real upload+complete, killed the worker container
(`docker kill -s SIGKILL`) ~0.4s later. Result: job observed stuck at
`RUNNING`, `progress: 0.12`, `stage: "Processing"` — **not** falsely
reported as succeeded. `lease_expires_at` confirmed 15 minutes in the
future via direct Postgres inspection (`docker compose exec postgres
psql`). Forced the lease into the past (the same technique the unit test
suite uses to avoid a literal 15-minute wait) to accelerate the scenario,
brought a worker back up, and within one `worker-beat` sweep tick (30s):
`{'leases_reclaimed': 1, 'redispatched': 1}`, followed by the job
completing (`attempt: 2`, `status: SUCCEEDED`, recording `READY`). No
duplicate `RecordingVersion`.

**Bug found and fixed during this test:** two overlapping `reclaim_jobs`
sweep executions (worker-beat catching up after the gap) both reported
`leases_reclaimed: 1` for the *same* job — `reclaim_expired_leases`
collected candidate rows via an unlocked `SELECT`, then updated by `id`
only, never re-checking `status`/`lease_expires_at` at write time. Fixed
by making the `UPDATE`'s `WHERE` clause restate the exact same condition
as the `SELECT` — a real compare-and-swap instead of a stale read
followed by a trusting write. Regression test:
`test_job_concurrency.py::test_concurrent_reclaim_sweeps_do_not_double_reclaim_the_same_job`
(manually interleaves two sessions' candidate reads before either write,
since a sequential re-run of the higher-level function cannot reproduce
the race).

### Duplicate delivery

Sent a second, genuinely duplicate `neuroworkspace.ingest` task message
for an in-flight job directly via `celery_app.send_task(...)` from inside
the worker container. Both messages were received; the first did the real
work (5.36s), the second returned in 5ms (claim refused — already
`SUCCEEDED`). Verified via direct SQL: exactly one `RecordingVersion` row,
`attempt: 1`.

### Duplicate HTTP mutation (Idempotency-Key)

Repeated `POST /recordings/{id}/processing-runs` with an identical
`Idempotency-Key` header. First call: `202`, new run created. Retry:
`200`, same run `id` returned. `GET .../processing-runs` confirmed exactly
one row.

### Concurrent annotation update

Two clients both read `revision=1` and both `PATCH` with that revision.
Result: one `200` (revision → 2), one real `409
annotation_revision_conflict` — not a silent overwrite. Final state
confirmed to be the winner's content.

### Job cancellation

Created a processing run and called `POST /jobs/{id}/cancel` immediately.
Caught it while still `QUEUED` (`attempt: 0`) — cancelled outright, final
state `CANCELLED`, no processing ever started, no orphaned
`ProcessingRun` (confirmed still `QUEUED`, never transitioned).

### Dependency outages (Redis, MinIO, Postgres)

- **Redis stopped:** waveform reads still returned `200` (cache fails
  open, confirmed live — not just in the `fakeredis` unit tests).
  `/health/ready` still reported ready (correct: reads and even durable
  writes do not need the broker). A new upload+complete during the outage
  still succeeded (`202`/`200`), with the job committed `QUEUED`,
  `dispatched_at: NULL`, and a structured `job_dispatch_failed` warning
  logged — not swallowed, not a failed request. Restarted Redis; within
  one sweep tick the job was redriven (`redispatched: 1`) and completed
  (`SUCCEEDED`) — the full transactional-outbox story proven end to end
  against a real broker outage.
- **MinIO stopped:** a waveform read hit an unhandled
  `botocore.exceptions.EndpointConnectionError` that reached Starlette's
  default handler as a **bare, unstructured `Internal Server Error` text
  body** — breaking this API's own documented error-envelope contract.
  `/health/ready` crashed the same way instead of reporting a clean `503`.
  **Both fixed**: added a catch-all `Exception` handler
  (`apps/api/src/neuro_api/main.py`) that logs the real exception
  server-side with a `diagnostic_id` and returns the standard
  `{"error": {...}}` envelope with `500`; wrapped `/health/ready`'s
  dependency checks so any failure reports a clean `503` with a specific
  code (`database_unavailable`, `demo_check_unavailable`) instead of
  crashing. Re-verified live after rebuilding: `500` with the proper
  envelope for the waveform read, `503 demo_check_unavailable` for
  readiness. Regression tests:
  `test_api_integration.py::test_unhandled_exception_returns_the_structured_error_envelope`,
  `::test_readiness_reports_503_not_a_bare_crash_when_db_is_unreachable`.
- **Postgres stopped:** `/health/ready` → `503 database_unavailable` with
  the proper envelope. Restarted Postgres; ready again with no API restart
  needed (`pool_pre_ping=True` handled reconnection transparently).

## Phase 4 — Object storage architecture

`docker inspect` on both the `api` and `worker` containers: **zero
mounts** on either — no shared volume of any kind (confirmed after
`docker-compose.yml`'s `canonical-data` volume was removed in an earlier
session; this pass re-confirmed it live rather than trusting the compose
file).

Listed the real object layout in MinIO for a published version:

```
canonical/<version_id>/manifest.json
canonical/<version_id>/metadata.json
canonical/<version_id>/signals.zarr/envelopes/L1/maximum/c/<channel>/<chunk>
canonical/<version_id>/signals.zarr/envelopes/L1/minimum/c/<channel>/<chunk>
canonical/<version_id>/signals.zarr/envelopes/L1/valid_count/c/<channel>/<chunk>
... (per pyramid level) ...
canonical/<version_id>/signals.zarr/raw/c/<channel>/<chunk>
```

Genuine per-chunk objects (~4 KB each), confirming range/chunk-based
storage, not one monolithic blob. Deleted a recording via the real API
and confirmed via `mc ls --recursive` that its entire `canonical/<version_id>/`
prefix was empty afterward — cleanup on delete verified against real
object storage, not asserted from the code. The opt-in
`tests/python/test_s3_signal_store.py` suite (write-from-one-instance/
read-from-another, delete, immutability rejection, partial-write leaves
nothing visible) was run for real against a live MinIO container this
session — **4/4 passed** (`uv run pytest tests/python -m s3_integration`),
closing the gap from the previous session where this suite was written but
never executed end to end.

## Phase 5 — Multiple replicas

**Workers:** `docker compose up -d --scale worker=2` started a second
worker container with no port conflict (workers publish no ports).
Submitted 3 concurrent ingestion jobs; all 3 reached `SUCCEEDED` with
`attempt: 1` — clean distribution across the 2-worker pool, no duplicate
processing.

**API:** Docker Compose's `ports:` merge is additive across `-f` override
files (not a replace), which blocked using `--scale api=2` with the base
compose file's fixed host port — documented here as a real Compose
authoring gotcha encountered, not a product bug. Worked around by running
a second, fully independent `api` container directly via `docker run`
(same network, distinct host port, no shared filesystem), which is
functionally identical to a second Compose-managed replica for this
purpose. Bringing this second replica up cleanly (no crash) is itself
confirmation that the Phase-1 `S3ObjectStore` retry fix (below) works.

**Bug found and fixed during this test:** the second API replica
initially crashed on startup with `botocore.exceptions.EndpointConnectionError`
from `S3ObjectStore.__init__`'s one-shot `head_bucket` check — reproduced
consistently across three separate attempts. `S3ObjectStore` performed
this check exactly once with no retry; the `create_bucket` fallback (used
in dev/test environments) had no error handling of its own either, so any
transient connectivity hiccup at container startup (DNS for the compose
service name not yet resolvable for a brand-new container, a race with
the dependency's own readiness) crashed the whole process immediately.
Fixed with bounded retry-with-backoff (`storage.py::S3ObjectStore._ensure_bucket`,
5 attempts, capped exponential backoff) around the entire
check-then-create sequence. Verified: rebuilt the image, brought up two
independent API containers back to back with no crash.

**Stateless proof**, both replicas live simultaneously:

- Signed in against replica 1 only; the signed session cookie was accepted
  by replica 2 with no server-side session store and no sticky routing
  (`GET /api/session` → `200` on both).
- Created a workspace via replica 1; immediately visible via replica 2
  (`GET /api/workspaces`) — shared Postgres, no replica-local cache of
  mutable state.
- Fetched the same recording's waveform via both replicas independently —
  identical channel data from both, each replica reading canonical Zarr
  data directly from MinIO with no coordination between them.
- A session from a *different* user correctly got `404` on a
  workspace-scoped recording it had no membership on, on both replicas —
  tenant isolation held under multi-replica load, not just single-process.

## Phase 6 — Kubernetes / Helm

**Not live-tested this session.** `helm lint` and `helm template` (both
fully client-side, no cluster) were run and pass (from the prior session
that authored the chart; unchanged since). Bringing up a local `kind`
cluster, building/loading images into it, and running `helm install` was
not attempted here — after the Docker-build flakiness already encountered
in Phase 1/5 (`uv sync` failing once for unclear reasons, requiring a
retry), doing so would have carried a real risk of consuming the rest of
this session's time without a clean result, and this report would rather
say "not tested" plainly than claim a rushed, uncertain pass. This is the
same limitation stated in `docs/DEPLOYMENT.md`'s Kubernetes section —
it has not changed. **Do not treat the Helm chart as proven to work
end-to-end; treat it as `helm template`-valid Kubernetes YAML that has
never been applied.**

**Update (later session):** the chart has since been installed and exercised
against a real local `kind` cluster — a real `helm install`/`helm upgrade`,
horizontal scaling to 2/3 API replicas with stateless behavior re-verified
live, and seven real Kubernetes-specific bugs found and fixed. This
superseded the "never been applied" statement above, which is left as-written
because it was true at the time this phase was run. See
[docs/KUBERNETES_VALIDATION.md](KUBERNETES_VALIDATION.md) for what was
actually proven and its own, still-current limitations (no production-grade
cluster, no live HPA cycle, sustained load testing cut short by host
resource contention).

## Phase 7 — Frontend/backend integration gaps

Not addressed this session (explicitly deprioritized in favor of
runtime-proving the backend, given the size of what Phases 1–5 alone
turned up). The gaps named in the task — cursor pagination only fetching
page one, job progress/cancellation UI, annotation conflict UX, stale
viewport request cancellation — remain open. `apps/web/src/lib/api.ts`'s
`annotations()`/`recordings()` still only consume the first page of the
now-paginated endpoints.

## Phase 8 — Full automated test suite (exact commands and results)

```
$ uv run pytest tests/python -m "not real_data and not s3_integration"
================= 55 passed, 10 deselected, 1 warning in 6.09s =================

$ uv run pytest tests/python -m s3_integration
================= 4 passed, 61 deselected, 1 warning in 3.47s ==================

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
112 files already formatted   (after `ruff format .` fixed 5 files this session)

$ uv run mypy packages/signal-core/src apps/api/src apps/worker/src
Success: no issues found in 38 source files

$ pnpm --filter @neuroworkspace/web typecheck
✓ Types generated successfully / tsc --noEmit clean

$ pnpm --filter @neuroworkspace/web test
Test Files  1 passed (1)  |  Tests  2 passed (2)

$ pnpm --filter @neuroworkspace/web lint
(clean, zero warnings)

$ DATABASE_URL=sqlite:///... alembic upgrade head   # from empty DB
4 revisions applied cleanly; `alembic check` reports no drift

$ DATABASE_URL=sqlite:///... alembic downgrade -1
Clean downgrade, no errors
```

Real-data acceptance tests (`-m real_data`) and browser-level E2E
(Playwright) were **not** run this session — out of scope for a
runtime-validation pass focused on the live Docker stack, and not
mentioned as failing; simply not exercised here.

**Total: 59/59 executed automated tests passing** (55 fast + 4 opt-in S3
integration), plus every scenario in Phases 2–5 above passing live against
the running stack outside the automated suite.

## Remaining issues (ranked, honestly)

1. **(Superseded — see the Phase 6 update above.)** Kubernetes/Helm has
   since been applied to a real local `kind` cluster; see
   [docs/KUBERNETES_VALIDATION.md](KUBERNETES_VALIDATION.md) for what was
   proven and what remains untested (production-grade cluster, live HPA
   cycle, sustained high-concurrency load).
2. **Frontend does not yet consume the pagination/job-status/cancellation
   surface the backend added.** The API is ready; the UI isn't wired to
   it.
3. **Two Docker build flakes observed but not fully root-caused**: one
   `uv sync --frozen --no-dev --no-cache` build failed once with no
   captured error detail (succeeded on immediate retry) — noted, not
   chased further, unlike the two fully-diagnosed bugs above.
4. **Real-data acceptance suite and Playwright E2E not run this session**
   — status unchanged from before, not verified either way here.
5. **`BadChannel` updates still last-write-wins** (no client-supplied
   revision to compare against) — a known, previously-flagged gap, not
   touched this session.

## Update: frontend integration and BadChannel concurrency (later session)

Items 2 and 5 above were addressed in a subsequent session, not reflected in
the phase-by-phase narrative above (which is left as-written, a snapshot of
that session):

- The frontend now consumes cursor pagination ("Load more" on recordings and
  annotations), job status/progress/cancellation (a reusable job-status view
  used by both the upload flow and a new processing panel), and a genuine
  conflict-recovery UX for stale annotation/bad-channel writes (409 →
  refetch current state → user reviews/reapplies, never a silent
  overwrite).
- `BadChannel` now uses the same compare-and-swap pattern as annotations:
  `BadChannelUpdate.revision` (0 for "not yet reviewed") is enforced with a
  conditional `UPDATE ... WHERE revision = :revision` (and the unique
  constraint backstops the concurrent-first-review race), returning `409
  bad_channel_revision_conflict` on a stale write. A new `GET
  /recordings/{id}/bad-channels` endpoint lists current state and revisions
  so the frontend can load and resync. No schema migration was needed — the
  `revision` column already existed as an audit counter; only its
  enforcement changed.
- A successful `average_reference` processing run now repoints
  `recording.active_version_id` at the derived version (mirroring
  ingestion's existing convention), so the derived `RecordingVersion` is
  actually reachable through the existing recording-scoped endpoints — this
  was a real gap: previously a derived version existed in the database but
  no route could ever serve it.
- See the top-level chat report from that session for the full list of
  frontend/backend integration gaps found and closed, exact Playwright and
  Vitest results, and remaining UX gaps (Kubernetes-on-a-live-cluster and
  the real-data suite's exact pass/fail counts are reported separately,
  not superseded here).
