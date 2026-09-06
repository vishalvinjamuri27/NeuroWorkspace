# Validation

What has actually been exercised against a running system, summarized by
category. Each section links to the detailed report with full evidence
(commands, raw output, exact reproduction steps) — this page is the index,
not a restatement of everything in those reports.

## Functional validation

A scripted client (`scripts/e2e_smoke.py`) drove the real running stack over
plain HTTP — sign-in, workspace/project creation, direct-to-object-storage
upload, ingestion, viewport reads, annotation create/edit, QC, processing,
export, and reopening the recording — all 24 steps passing against a live
Docker Compose stack with real Celery dispatch (not eager/in-process
execution). See "Phase 2" in [RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md).

Separately, a real Chromium browser (Playwright) drove the same core
workflow through the actual UI, including a full pan/zoom/annotation/QC/
processing pass and adversarial input (XSS payloads, malformed uploads,
cross-tenant access attempts). See
[ADVERSARIAL_BROWSER_VALIDATION.md](ADVERSARIAL_BROWSER_VALIDATION.md).

## Real-data validation

Format adapters are tested against checksum-pinned public files, not just
synthetic fixtures — see [README.md](../README.md#supported-formats) for the
current list and sources (`testdata/manifest.yaml`,
`scripts/fetch_test_data.py`).

Beyond the pinned acceptance fixtures, three real, previously-unseen
whole-night PhysioNet Sleep-EDF recordings (~23 hours each, 7 channels) were
uploaded through the browser and taken through the full
upload→ingest→viewer→annotate→process→reopen path, specifically to check
that behavior doesn't depend on characteristics of any one already-tested
file. See [UNSEEN_DATA_VALIDATION.md](UNSEEN_DATA_VALIDATION.md) for exact
sources/hashes and
[ADVERSARIAL_BROWSER_VALIDATION.md](ADVERSARIAL_BROWSER_VALIDATION.md) for
results.

## Concurrency validation

- **Job claiming**: 32,000 concurrent claim attempts (16 real worker
  threads racing already-`RUNNING` jobs) — zero jobs claimed more than
  once, confirming the compare-and-swap claim path holds under genuine
  concurrent Postgres connections, not just interleaved calls on one
  connection. See "Job-system database performance" in
  [PERFORMANCE.md](PERFORMANCE.md).
- **Annotation writes**: two real concurrent HTTP clients editing the same
  annotation — one succeeds, the other gets a real `409` with the current
  server state, never a silent overwrite. See "Concurrent annotation
  update" in [RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md).
- **Worker crash recovery**: a worker was killed mid-job with `SIGKILL`;
  the lease expired and `worker-beat` redrove the job within one sweep
  tick, with no duplicate `RecordingVersion`. This exercise also found and
  fixed a real double-reclaim race in the lease-reclaim sweep itself. See
  "Worker crash mid-ingestion" in
  [RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md).
- **Frontend double-submit**: a same-tick double click on "Run average
  reference" could mint two `ProcessingRun`s before a fix closed the race
  with a synchronous guard (the `disabled` attribute alone wasn't
  sufficient, since it depends on a React render that isn't guaranteed to
  land before a second click event). See
  [ADVERSARIAL_BROWSER_VALIDATION.md](ADVERSARIAL_BROWSER_VALIDATION.md).

## Performance validation

Query plans, pagination depth, viewport latency, and job-claim throughput
were all measured directly against a seeded 8.4M-row database and real
S3-backed recordings — not assumed from the code. The one real bottleneck
found (a single-process API under concurrent load) is documented alongside
what was checked and found to *not* be a problem. Full methodology,
environment, and every number: [PERFORMANCE.md](PERFORMANCE.md).

## Deployment validation

- **Docker Compose**: a clean `docker compose up --build` from a
  torn-down state brings up all services, runs migrations, and seeds the
  sample recording. Two real build/runtime bugs (a Dockerfile layer-caching
  issue, a Celery-prefork/fsspec fork-safety issue) were found and fixed
  during this pass. See "Phase 1" in
  [RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md).
- **Kubernetes**: the Helm chart has been installed and exercised against a
  real local `kind` cluster — a clean install, a real `helm upgrade` that
  preserved existing data, and 1/2/3-replica API scaling with the full
  workflow verified stateless across replicas. Seven real Kubernetes-specific
  bugs were found and fixed. See
  [KUBERNETES_VALIDATION.md](KUBERNETES_VALIDATION.md).

## Security validation

Tenant isolation (zero-membership and insufficient-role cases), stored-XSS
inertness, malformed/hostile upload handling, and origin/session-cookie
enforcement were all exercised against a real browser and real API — see
[ADVERSARIAL_BROWSER_VALIDATION.md](ADVERSARIAL_BROWSER_VALIDATION.md). See
also [THREAT_MODEL.md](THREAT_MODEL.md) for the design-level trust-boundary
analysis this validation was checked against.

## What hasn't been validated

- Any production-grade cluster (EKS/GKE/AKS) or the externally-provisioned
  Postgres/Redis/S3 path — only a local `kind` cluster and Docker Compose's
  bundled dependencies have been exercised.
- Real concurrent user traffic at any meaningful scale — the load test in
  [PERFORMANCE.md](PERFORMANCE.md) measured one synthetic tenant's request
  mix against one API process on one laptop; it establishes where a
  bottleneck is, not a number of users this system supports.
- Sustained high-concurrency load on Kubernetes (cut short in the one
  attempt by host resource contention from running the cluster and the
  Compose stack on the same machine — see
  [KUBERNETES_VALIDATION.md](KUBERNETES_VALIDATION.md)).
- A formal external security audit.
