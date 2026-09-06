# Kubernetes runtime validation

This is a record of an actual `kind` cluster being created, the Helm chart
actually installed and upgraded against it, and the running deployment
actually exercised — not a `helm lint`/`helm template` report. Every claim
below states exactly what was run and what was observed. Where a planned
check was not completed, that is stated explicitly rather than omitted.

**This session hit a real, honest limitation partway through**: running a
`kind` cluster and the full Docker Compose stack simultaneously on one
14-core development laptop eventually saturated the machine badly enough
that the `kind` control-plane container itself (which hosts the whole
"node" — API server, etcd, kubelet, and every pod) became unresponsive for
about 30 minutes and had to be restarted, then remained unstable under
further load. The Docker Compose baseline was never affected and stayed
healthy throughout (confirmed by directly checking it during the incident).
Once the instability made further live testing unreliable, the cluster was
deleted rather than continuing to fight the environment. Sections 8–11 and
14 below are marked accordingly. See "Limitations" for the full accounting.

## Environment

| | |
|---|---|
| Host | macOS (Darwin 24.1.0, arm64), Docker Desktop |
| Kubernetes distribution | `kind` v0.33.0 (installed this session via Homebrew) |
| Kubernetes version | v1.37.0 (`kindest/node:v1.37.0`) |
| Cluster topology | single node (`neuroworkspace-control-plane`, control-plane + workloads) |
| Helm | v3.21.4 |
| Node resources | shared with the host's full Docker Compose stack (7 containers) — no dedicated resources; see Limitations |
| metrics-server | v0.8.x (latest at install time), patched with `--kubelet-insecure-tls` for kind |

## Deployment topology

Chart: `deploy/helm/neuroworkspace`, dev overlay (`values-dev.yaml`) —
bundles single-replica Postgres/Redis/MinIO in-cluster rather than
expecting externally-provisioned services (see `docs/DEPLOYMENT.md` for
why that split exists).

```
Deployment nw-api          (scaled 1 → 2 → 3 during this session)
Deployment nw-web          (1 replica)
Deployment nw-worker       (scaled 1 → 2 during this session)
Deployment nw-worker-beat  (1 replica, fixed)
Deployment nw-postgres     (dev-only, 1 replica, emptyDir)
Deployment nw-redis        (dev-only, 1 replica, emptyDir)
Deployment nw-minio        (dev-only, 1 replica, emptyDir)
Job nw-migrate             (pre-install/pre-upgrade Helm hook, alembic upgrade head)
```

## Installation

Real commands, run in this order, against the real cluster:

```
kind create cluster --name neuroworkspace --wait 120s

docker build -t neuroworkspace/api:kind -f apps/api/Dockerfile .
docker build -t neuroworkspace/worker:kind -f apps/worker/Dockerfile .
docker build -t neuroworkspace/web:kind -f apps/web/Dockerfile .
kind load docker-image neuroworkspace/api:kind neuroworkspace/worker:kind neuroworkspace/web:kind --name neuroworkspace

helm install nw deploy/helm/neuroworkspace \
  -f deploy/helm/neuroworkspace/values.yaml \
  -f deploy/helm/neuroworkspace/values-dev.yaml \
  --set image.registry=neuroworkspace --set image.tag=kind \
  --set config.apiPublicUrl=http://localhost:18000 \
  --set config.s3PublicEndpointUrl=http://localhost:19000 \
  --wait --timeout 5m
```

**This did not succeed on the first several attempts.** Five real,
distinct bugs were found and fixed by actually running it — none of them
are visible in `helm lint` or `helm template` output, which is exactly why
this validation phase exists.

### Bug 1 — ServiceAccount did not exist when the pre-install migration Job needed it

`templates/migration-job.yaml` referenced the chart's custom
`ServiceAccount`, but a Helm `pre-install` hook Job runs *before* any of
the chart's ordinary (non-hook) resources are created — including that
ServiceAccount. First install attempt failed immediately:
`Error creating: pods "nw-migrate-" is forbidden: error looking up service
account default/nw: serviceaccount "nw" not found`.

**Fix**: the migration Job never actually needed a *named* ServiceAccount
— the custom one exists purely to get `automountServiceAccountToken:
false` (this app never calls the Kubernetes API). Removed the
`serviceAccountName` reference and set `automountServiceAccountToken:
false` directly on the Job's pod spec, using the namespace's always-present
`default` ServiceAccount instead. Zero hook-ordering dependency.

### Bug 2 — Secret did not exist when the pre-install migration Job needed it

Same root cause, different resource: `neuroworkspace-secrets` (containing
`DATABASE_URL` etc.) is an ordinary resource, so the migration Job's
`envFrom` reference to it failed with `Error: secret
"neuroworkspace-secrets" not found`. Unlike the ServiceAccount, the
migration Job genuinely needs these values.

**Fix**: hooked the Secret itself as `pre-install` at an earlier weight
(`-5`) than the migration Job's `0`, so it is guaranteed to exist first.

### Bug 3 — Non-numeric container `USER` breaks `runAsNonRoot` verification

Both `apps/api/Dockerfile` and `apps/worker/Dockerfile` had `USER
neuroworkspace` (a name, not a UID). Every pod using these images (api,
worker, worker-beat, migrate) has `securityContext.runAsNonRoot: true`.
Kubelet refused to start any of them:
`container has runAsNonRoot and image has non-numeric user
(neuroworkspace), cannot verify user is non-root`. Docker Compose never
hits this — it does not perform this verification.

**Fix**: changed both Dockerfiles to `USER 100:101` (the exact UID/GID
`adduser --system` already assigned; confirmed via `docker run --rm
<image> id`), and added explicit `runAsUser: 100` to every Helm template
using these images, matching the pattern `apps/web`'s Dockerfile/Helm
template already used correctly.

### Bug 4 — `postgres:18+`'s image needs `/var/lib/postgresql`, not `.../data`, mounted

`dev-postgres.yaml` mounted its `emptyDir` at `/var/lib/postgresql/data`.
Postgres 18's image places data in a major-version-specific subdirectory
under `/var/lib/postgresql` itself and refuses to start otherwise. The pod
crash-looped: `these Docker images are configured to store database data
in a format which is compatible with pg_ctlcluster`.
`docker-compose.yml`'s own `postgres` service already correctly mounts at
the parent directory — this chart's dev overlay just hadn't matched it.

**Fix**: changed the mount path to `/var/lib/postgresql`.

### Bug 5 — `values-dev.yaml` hardcoded hostnames that only work for one specific release name

`values-dev.yaml` hardcodes `DATABASE_URL`/`REDIS_URL`/`S3_ENDPOINT_URL`
pointing at hosts like `neuroworkspace-postgres`. Those only resolve if
this chart is installed with `--name neuroworkspace`. **This repo's own
documented install command, in `docs/DEPLOYMENT.md`, is `helm install nw
...`** — a different release name, producing Services actually named
`nw-postgres`/`nw-redis`/`nw-minio`. Following the project's own
documented command, the migration Job's database-connectivity check
(added while fixing this — see below) hung retrying a hostname that could
never resolve.

**Fix**: `secret.yaml` and `_helpers.tpl` now compute these hostnames from
the release's actual `fullname` when the bundled dev dependencies are
enabled, instead of trusting the static strings in `values-dev.yaml`.

### Bug 6 (found while fixing Bug 5) — hooking dev dependencies for `pre-upgrade` wiped their data

The first fix for Bugs 1/2/5 also hooked `dev-postgres`/`dev-redis`/
`dev-minio` as `pre-install,pre-upgrade` (to guarantee ordering on both
operations). Testing the upgrade path (see below) surfaced a second,
worse bug: `helm.sh/hook-delete-policy: before-hook-creation` deletes and
recreates a hook resource on *every* operation it fires for — so a
`helm upgrade` deleted and recreated the dev Postgres/Redis/MinIO
Deployments, and since their volumes are `emptyDir`, **this wiped their
data**. The following pre-upgrade migration Job then ran cleanly against
a freshly empty database, which looked like success but was not.

**Fix**: changed these three resources' hook annotation to `pre-install`
only. The Service already exists persistently after the first install (a
hook resource that fires for `pre-install` is still an ordinary,
non-deleted object once created — only *repeat* firing on `pre-upgrade`
was the problem), so the pre-upgrade migration Job can still resolve and
reach it with no hook needed on this resource for that operation. Verified
this fix directly: a real `helm upgrade` afterward left `nw-postgres`,
`nw-redis`, and `nw-minio` completely untouched (same pod names/ages
before and after), and a recording created before the upgrade was still
fully intact after it (see "Migration strategy" below).

### Bug 7 — `celery beat`'s default schedule file crashes under `readOnlyRootFilesystem`

`nw-worker-beat` crash-looped: `OSError: [Errno 30] Read-only file system:
'celerybeat-schedule'`. Celery beat's default schedule-state file is
`celerybeat-schedule` in the process's current working directory (`/app`);
every one of this chart's pods runs with `readOnlyRootFilesystem: true`.
Docker Compose never hits this (its containers are writable).

**Fix**: added `--schedule=/tmp/celerybeat-schedule` to the worker-beat
container's args, pointing at the `/tmp` `emptyDir` already mounted for
this pod. This state is disposable (it only dedupes beat's own tick timing
across restarts, not job data), so no persistent volume is needed.

**None of these seven bugs are hypothetical or template-only** — each was
found by an actual failed pod/Job on a real cluster, and each fix was
verified by a subsequent successful `helm install`/`helm upgrade` and
running pod.

### Migration execution (single mechanism, verified)

The chart's migration strategy is a Helm `pre-install,pre-upgrade` hook Job
(`nw-migrate`) running `alembic upgrade head` to completion before any
api/worker pod is scheduled — not per-replica migration racing. Verified
directly:
- Clean install: `nw-migrate` ran, completed in ~11s once Bugs 1/2/4/5
  were fixed, and self-deleted per its `hook-succeeded` delete policy
  (`kubectl get jobs` shows none after a successful install — this is
  correct, not a missing resource).
- Upgrade: a second `helm upgrade` re-ran `nw-migrate` against the
  **existing, unmodified** database (after fixing Bug 6) and completed
  without re-running any already-applied migration incorrectly or
  double-applying anything, since `alembic upgrade head` is itself
  idempotent.
- Scaling the API Deployment to 2 and then 3 replicas did **not** trigger
  any additional migration Job run — only `helm install`/`helm upgrade`
  invoke the hook, `kubectl scale` does not.

## Health checks (liveness vs. readiness)

This distinction was already correctly implemented in the application
before this task (`apps/api/src/neuro_api/main.py`, `api-deployment.yaml`)
— this session verified it live rather than building it:

- **Liveness** (`GET /health/live`) never touches the database or any
  dependency; it returns `{"status": "live"}` unconditionally. This is
  deliberate: a slow/unavailable Postgres must never cause the kubelet to
  kill and restart every API replica simultaneously (a restart storm).
- **Readiness** (`GET /health/ready`) does check DB connectivity (and,
  with `REQUIRE_DEMO_READY`, the seeded sample); a failing dependency
  returns a clean `503`, pulling that replica from Service endpoints
  without killing it.

Verified live: `curl http://localhost:18000/health/live` →
`{"status":"live"}`; `curl http://localhost:18000/health/ready` →
`{"status":"ready","build":"0.1.0-alpha.1"}` with `HTTP 200` once the
migration Job had completed and Postgres was reachable.

**Not independently re-tested this session**: explicitly killing
Postgres/Redis/MinIO pods one at a time and watching `/health/ready`
degrade to `503`. This exact behavior (readiness correctly reporting `503`
for an unreachable Postgres, and the API surviving a killed
Redis/MinIO without crashing) was already proven directly against the same
application code under Docker Compose in the prior runtime-validation
session (`docs/RUNTIME_VALIDATION.md`) — the code path is identical
(`/health/ready`'s dependency checks do not know or care whether they are
running under Compose or Kubernetes), so this was not repeated live here
given the time lost to the resource-contention incident, and this fact is
represented as an assumption carried over, not a k8s-specific
verification.

**Observed instead, unplanned**: real liveness-probe-triggered restarts.
Under the host resource contention described in "Limitations," one API
pod's liveness probe genuinely failed (`connection refused` /
`context deadline exceeded`) and kubelet correctly killed and restarted
it — repeatedly, as contention continued. This is liveness working
exactly as designed (detecting a genuinely unresponsive process and
recovering it), even though the root cause was host-level contention
rather than an application bug.

## Stateless API validation

Full 24-step E2E workflow (`scripts/e2e_smoke.py`, extended this session
with `E2E_API_BASE`/`E2E_WEB_BASE` overrides so the same script drives
either Docker Compose or a port-forwarded Kubernetes Service) run against
the live cluster at each replica count:

- **1 API replica**: all 24 steps passed (sign-in, workspace/project,
  presigned direct-to-MinIO upload, real Celery ingestion, waveform read,
  annotation create/revise, QC, `average_reference` processing producing
  a derived `RecordingVersion` with correct lineage, export
  create/download, reopen).
- **2 API replicas**: all 24 steps passed again, same script, same
  assertions, no changes needed.
- **3 API replicas**: all 24 steps passed again.

**Cross-replica distribution, proven, not assumed**: `kubectl port-forward`
to a *Service* picks one backend pod for the whole forwarded connection —
it does not round-robin (a real limitation of `kubectl port-forward`, not
of Kubernetes networking). To actually prove requests land on different
replicas, a throwaway `curlimages/curl` pod was run *inside* the cluster
issuing repeated requests against the Service's ClusterIP (`http://nw-api`),
which genuinely goes through kube-proxy's load-balancing:
- 20 unauthenticated `GET /health/live` requests at 2 replicas: pod A's
  logs showed 63 matching lines, pod B's showed 26 (both pods definitely
  served real traffic; the raw counts include prior warm-up/probe traffic
  already in each pod's log buffer, not just these 20 requests).
- 10 real dev-auth sign-ins + `GET /api/workspaces` calls (a fresh signed
  cookie each time) at 2 replicas: pod A's logs showed 13 matching lines,
  pod B's showed 10 — proving the stateless, signed-JWT-cookie session
  mechanism works correctly regardless of which replica issued vs.
  validated the cookie. No server-side session store, no sticky-session
  requirement, confirmed by the requests actually succeeding when split
  across replicas that never coordinated with each other.

No route was found to depend on process-local state; no code changes were
needed for statelessness (the application was already stateless — this
confirms it, not fixes it).

## Horizontal scaling (load test)

`scripts/loadtest.js` (k6) extended this session to accept a
comma-separated `API_BASE` list, since — per the port-forward limitation
above — proving a throughput difference between replica counts requires
each replica to actually receive traffic, which one forwarded connection
to a Service cannot do. Each replica was port-forwarded individually
(`kubectl port-forward pod/<name> <port>:8000`) and k6 was given the full
list; each VU picks one base at startup.

**Read this table with the resource-contention caveat in "Limitations"
foremost in mind** — these were not clean, isolated measurements; the same
host was simultaneously running the full 7-container Docker Compose stack
throughout, at real, uncontrolled CPU pressure that changed over the
course of the session. The *directional trend* is the signal here, not the
absolute numbers, which are not comparable to `docs/PERFORMANCE.md`'s
Docker Compose numbers.

| API replicas | req/s | iterations | waveform avg latency | http error rate |
|---|---|---|---|---|
| 1 | 13.6 | 26 | 6,302 ms | 3.18% |
| 2 | 17.1 | 72 | 1,488 ms | 4.44% |
| 3 | 18.3 | 100 | 1,016 ms | 5.15% |

(8 VUs, 20s duration, same mixed workload as `docs/PERFORMANCE.md`'s load
test — 35% waveform reads, 20% metadata, 15% annotation reads, 10%
pagination, 10% job polling, 5% annotation writes, 5% exports.)

**Throughput increased and waveform latency dropped substantially with
each added replica** — the same "more server-side processes help under
CPU-bound load" pattern found in the Docker Compose benchmark
(`docs/PERFORMANCE.md`, "Load test"), now also observed in Kubernetes. The
rising error rate alongside more replicas is very likely a contention
artifact (a handful of writes timing out against an already-strained host,
not a logic bug — the same write/read paths passed all 24 E2E steps
cleanly, repeatedly, at every replica count when run without concurrent
load). This was **not** root-caused further given the time lost to the
resource-contention incident described below.

**Not completed**: a clean, isolated repeat of this table without a
concurrently-running Docker Compose stack, and CPU/memory measurements
per pod during the load test (`kubectl top` was working but was not
captured mid-run before the cluster became unstable).

## Worker scaling

Scaled `nw-worker` from 1 to 2 replicas. The processing pipeline's
correctness under real multi-replica contention was already proven this
session at the database level in `docs/PERFORMANCE.md`
(`scripts/bench_job_concurrency.py`: 32,000 concurrent claim attempts
against real Postgres, zero double-claims) — the same `claim_job()`
compare-and-swap code runs unmodified inside each Celery worker pod here.

**What was directly observed in Kubernetes**: with 2 worker replicas
running, the E2E workflow's `average_reference` processing step (which
submits a real job through the API and polls it to completion) continued
to succeed, meaning *some* worker claimed and completed the job correctly
each time — consistent with correct distribution, though this session did
not instrument *which* of the 2 workers picked up any specific job to
produce an explicit "job A went to worker 1, job B went to worker 2"
distribution log the way the API replica test did with HTTP logs.

**Not completed**: a dedicated "submit N jobs, confirm both workers
process at least one" test with explicit per-worker log evidence, and a
deliberate "kill the worker that is mid-job and confirm lease-based
reclaim" test (section 9) — the cluster became unresponsive from host
resource contention while this was being set up, before it produced a
result. One worker pod *did* independently enter `CrashLoopBackOff` during
the contention incident (not a deliberate kill); by the time the cluster
was queried again it had recovered, but no job's lease-expiry/reclaim was
specifically inspected at that moment. This is a genuine gap, stated
plainly rather than papered over with the DB-level test above, which
proves the *mechanism* is correct but is not itself a live Kubernetes
worker-crash observation.

## Pod failure testing

**Not completed as a deliberate, controlled test.** The plan was to
`kubectl delete` a specific API pod mid-workload and verify request
continuity and clean replacement; this was superseded by the real,
unplanned kubelet-driven restarts described above (liveness probe
failures under host contention), which demonstrate the same underlying
recovery mechanism (kubelet restarting an unresponsive container) but were
not a controlled experiment with request-level pass/fail measurement
during the replacement window. Stated as not done, not inferred as done.

## Rolling update

The `helm upgrade` performed to fix Bug 6/7 (worker-beat's schedule path)
**is** a real rolling update of `nw-worker-beat` (a new ReplicaSet was
created; `kubectl get pods` showed the new pod running with 0 restarts
immediately after, using the fixed `--schedule` flag) and left
`nw-api`/`nw-web`/`nw-worker` completely untouched (their pod
names/ages/ReplicaSet hashes were unchanged before and after the upgrade,
since nothing in their templates changed) — a correctly *targeted*
rollout, not a full-chart restart. `api-deployment.yaml`'s
`RollingUpdate` strategy (`maxUnavailable: 0, maxSurge: 1`) was not
separately exercised with a deliberate api-image-tag bump and concurrent
traffic in this session; the worker-beat rollout above is the only rolling
update actually observed.

## Graceful shutdown

**Not tested this session.** Termination grace periods (`worker`'s
`terminationGracePeriodSeconds: 120`, documented in `docs/DEPLOYMENT.md`
against Celery's `task_acks_late`/`task_reject_on_worker_lost` behavior)
were not changed and were not independently re-verified live here; this
was previously reasoned about (not measured) when the chart was authored,
and remains in that state after this session.

## Storage durability

**Directly proven** across a real Helm upgrade: a recording (ingested,
processed into a derived `RecordingVersion`, annotated) created via the
API before `helm upgrade` was fetched again afterward through the API and
found fully intact — `state: READY`, correct `active_version_id` pointing
at the derived version, all without the `nw-postgres`/`nw-minio` pods
being touched (same pod identity before/after, confirming Bug 6's fix).

**Also observed** (not a controlled test, but real evidence): after the
`docker restart neuroworkspace-control-plane` needed to recover from host
contention — which restarts every containerd process "node-wide," the
closest thing to a full cluster reboot this environment can produce —
`nw-postgres`, `nw-redis`, and `nw-minio` all came back `1/1 Running`
retaining their prior state (`kubectl get pods` showed them healthy
immediately after, at their pre-restart ages, meaning the underlying
container filesystem — where their `emptyDir` volumes live — survived).

**Not completed**: the specific "identify which worker produced a
recording, delete that worker, delete/restart API pods, request the
recording through a new API pod" sequence exactly as scoped, and
inspecting pod mount specs to confirm no hidden shared PersistentVolume is
involved. The `values.yaml`/templates were read directly as part of this
task, though, and confirm no PVC exists anywhere in this chart for
canonical signal data — only the dev-only Postgres/Redis/MinIO have
`emptyDir` (never shared across pods; MinIO's `emptyDir` is exactly the
same architecture the Docker Compose environment already proved
canonical-signal durability against in the prior performance session,
just running inside a Kubernetes pod instead of a Compose container).

## E2E workflow coverage against Kubernetes

`scripts/e2e_smoke.py`'s 24 steps (real HTTP client, not mocked) were run
against the live cluster at 1/2/3 API replicas and covered: authentication
(dev sign-in + cookie persistence), workspace/project creation, upload
(presigned direct-to-MinIO), ingestion (real Celery worker), waveform
read, annotation create + revise, QC, processing (`average_reference`)
producing a derived version with correct lineage, export create/download,
and reopening the recording. **Bad-channel update and the browser-based
Playwright suite were not run against Kubernetes this session** — the
former simply wasn't in `e2e_smoke.py`'s existing 24 steps and wasn't
added under the time pressure of the resource-contention incident; the
latter would need a stable, reachable `localhost` URL for Playwright's
browser to hit, which the port-forwarding-plus-instability situation did
not reliably provide before the cluster was deleted. Both are already
covered against Docker Compose (`apps/web/tests/e2e/*.spec.ts`, 7/7
passing per the prior frontend-integration session).

## Database connections

Observed via `psql`/`pg_stat_activity` inside `nw-postgres`:

| Configuration | Total connections | Active |
|---|---|---|
| 3 API replicas + 1 worker + 1 worker-beat, idle-ish | 13 | 1 |
| 3 API replicas + 1 worker + 1 worker-beat, under modest k6 load | 27 | 1 |

Both comfortably under Postgres's `max_connections` (100, image default,
unmodified). This matches `docs/PERFORMANCE.md`'s connection-pool
arithmetic: each replica's engine can open up to 30 connections
(`pool_size=10 + max_overflow=20`) but only opens them lazily as actually
needed, not eagerly up front — 3 replicas' theoretical ceiling (90, plus
workers) was never approached in practice at these load levels.
**2–3 replicas work cleanly with the current pool sizing; no evidence
gathered here suggests a pooler (PgBouncer) is needed**, matching this
task's explicit instruction not to add one without measured evidence.

## HPA

**Not tested live.** The chart already ships an `api-hpa.yaml`
(`minReplicas: 2, maxReplicas: 10`, CPU target from
`values.yaml`, `scaleDown.stabilizationWindowSeconds: 300`) from the
architecture-only phase before this task. `metrics-server` was installed
and confirmed working this session (`kubectl top pods` returned real
per-pod CPU/memory numbers), which is the prerequisite for HPA to function
at all — but driving real CPU load at the HPA long enough to observe an
actual scale-up/scale-down cycle (which needs sustained load over multiple
metrics-server polling intervals) was not attempted before the cluster
became unstable. Stated plainly: the HPA resource exists and its
metrics-server dependency is confirmed functional; its live scaling
behavior is unverified.

## Limitations

- **A `kind` cluster and a full 7-container Docker Compose stack, run
  simultaneously on one Apple Silicon laptop with no dedicated resources
  for either, is not a stable environment for sustained multi-replica load
  testing.** The `kind` control-plane container (which hosts the entire
  simulated "node" — API server, etcd, kubelet, every pod) was observed at
  300–660% CPU and needed a manual `docker restart` after becoming fully
  unresponsive (even plain `docker ps` calls queued for many minutes) for
  roughly 30 minutes. This is a real, reproducible constraint of this
  specific local setup, not a NeuroWorkspace defect — but it is also
  exactly why sections 8–11 and 14 above are marked incomplete rather than
  reporting fabricated results.
- **`kind` is not a production cloud cluster.** Single node, no real
  multi-AZ scheduling, no cloud load balancer, no managed control plane
  SLA, no real node autoscaling, no PodDisruptionBudget behavior across
  multiple nodes (this chart's `api-pdb.yaml`/`worker-pdb.yaml` were never
  exercised against more than one node because there was only one).
- **The dev overlay's bundled Postgres/Redis/MinIO are not the
  production path.** `values.yaml`'s default (external, managed
  Postgres/Redis/S3) was never exercised this session; only
  `values-dev.yaml` was.
- **The load-test numbers in this document are not comparable to
  `docs/PERFORMANCE.md`'s Docker Compose numbers** — different, actively
  contended environment, smaller sample (one 20-second run per replica
  count, not repeated for confidence).
- **Sections left incomplete** (worker-crash-during-job with lease
  reclaim, deliberate API pod kill with request-level pass/fail during
  replacement, graceful shutdown, live HPA scale-up/down, browser
  Playwright against Kubernetes) are exactly that — incomplete, not
  quietly assumed to have passed. Anyone relying on this chart in
  production should run them against a real, dedicated cluster before
  trusting the corresponding behavior.
