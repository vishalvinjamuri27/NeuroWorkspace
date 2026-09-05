# Kubernetes deployment

The Helm chart at [`deploy/helm/neuroworkspace`](../deploy/helm/neuroworkspace)
is the production deployment path; `docker-compose.yml` remains the local
single-machine path (native `make dev`, or the full Compose stack). This
document explains what the chart does and, as importantly, what it does not
do and why.

## Validation status

Every manifest in this chart is checked with `helm lint` and `helm template`
(see `make helm-lint` / `make helm-template`). Beyond that static check, the
dev overlay (`values-dev.yaml`) has been installed and exercised against a
real local `kind` cluster: a clean `helm install`, a real `helm upgrade`
that preserved existing Postgres data, horizontal scaling of the API
Deployment to 2 and 3 replicas with the full application workflow verified
stateless across them, and a real pod restart recovering cleanly. That pass
found and fixed five real bugs `helm lint`/`helm template` could not have
caught — see [`docs/KUBERNETES_VALIDATION.md`](KUBERNETES_VALIDATION.md) for
exactly what was run, what broke, and what is still unverified (sustained
load testing at high concurrency was cut short by host resource contention
on the single machine running both this cluster and the Docker Compose
stack side by side; no production-grade cluster (EKS/GKE/AKS) has been
used, and the chart's non-dev path — external Postgres/Redis/S3 — has not
been exercised end-to-end). Treat the non-dev/production path the same way
you'd treat any infrastructure code nobody has actually run yet.

## Topology

```
Ingress (nginx/cert-manager)
  ├─ api.example.com   → Service nw-api   → Deployment nw-api   (HPA 2–10, CPU 70%)
  └─ app.example.com   → Service nw-web   → Deployment nw-web   (HPA 2–8,  CPU 75%)

Deployment nw-worker       (HPA 2–20, CPU 75%)  — Celery worker, no ingress
Deployment nw-worker-beat  (fixed at 1)         — outbox dispatcher / lease-reclaim sweep
Job nw-migrate             (pre-install/pre-upgrade hook) — alembic upgrade head

External (not in this chart): PostgreSQL, Redis, S3-compatible bucket
```

## Connection pool ceiling vs the API HPA's max replica count

`docs/PERFORMANCE.md` measured (not assumed) that the API's SQLAlchemy
engine needs an explicit `pool_size=10, max_overflow=20` (30 connections
per process) to avoid connection-checkout timeouts under realistic
concurrency — SQLAlchemy's own un-set defaults (15) started failing at 16
concurrent DB-touching requests on one process. At this chart's API HPA
ceiling of **10 replicas**, that is up to **300 connections** against a
managed Postgres whose default `max_connections` is commonly 100-200
depending on instance size — a real capacity mismatch this chart does not
resolve for you. Before scaling the API HPA's `maxReplicas` up in a real
deployment, either raise the managed Postgres's `max_connections`
accordingly, lower `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` per replica, or put a
connection pooler (e.g. PgBouncer) in front of it — this chart currently
does none of the three.

## Why external Postgres/Redis/S3 by default

`values.yaml` defaults to `devDependencies.enabled: false` and expects
`DATABASE_URL`/`REDIS_URL`/`S3_*` to point at externally-provisioned
services (RDS/Cloud SQL, ElastiCache/MemoryStore, S3/GCS). Running your
database of record as a Helm-bundled in-cluster StatefulSet is a common
resume-project shortcut that a real production deployment does not take —
it means reinventing backup/PITR, failover, and storage-class correctness
that a managed service already solves. `values-dev.yaml` layers on
single-replica, `emptyDir`-backed Postgres/Redis/MinIO Deployments (see
`templates/dev-*.yaml`) so the chart is still fully runnable end-to-end on a
local `kind`/`minikube` cluster — explicitly marked dev-only, not a
production degradation path.

```bash
helm install nw deploy/helm/neuroworkspace \
  -f deploy/helm/neuroworkspace/values.yaml \
  -f deploy/helm/neuroworkspace/values-dev.yaml \
  --set image.registry=localhost:5001/neuroworkspace
```

## Secrets

`secrets.create: false` by default — the chart expects a Secret named
`neuroworkspace-secrets` (configurable) to already exist, created out of
band (`kubectl create secret generic`, a sealed-secrets/SOPS-encrypted
manifest applied separately, or an External Secrets Operator syncing from a
cloud secret manager), with keys `DATABASE_URL`, `REDIS_URL`,
`SESSION_SECRET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `OIDC_CLIENT_SECRET`.
`values-dev.yaml` sets `secrets.create: true` and creates the Secret from
plaintext values for local convenience — never do that against a real
cluster; those values would be recoverable from Helm's release history
(stored as a Secret in-cluster, but still plaintext-readable by anyone with
`get secrets` on the release).

## Job system continuity: why this deploys safely under normal k8s churn

This is the part of the chart most worth walking through in an interview,
because it is not new infrastructure code hoping for the best — it is a
direct consequence of the job-system correctness work already in
`services.jobs` and `services.jobs.LeaseHeartbeat`:

- **Rolling deploys / node drains kill a worker pod mid-job.** Celery's
  `task_acks_late=True` + `task_reject_on_worker_lost=True`
  (`neuro_worker/celery_app.py`) means the broker redelivers the task.
  `claim_job()`'s conditional `UPDATE` (a single atomic CAS) makes picking
  it back up effectively-once regardless of which replica claims it next —
  see `terminationGracePeriodSeconds: 120` on the worker Deployment, chosen
  to give an in-flight job room to reach its next `LeaseHeartbeat`-covered
  checkpoint, not to guarantee zero interruption, which the job system does
  not need in the first place.
- **A worker pod is OOMKilled or the node fails outright** (no graceful
  SIGTERM at all). The job's lease expires; `worker-beat`'s sweep
  (`services.jobs.run_dispatch_sweep`, `reclaim_expired_leases`) resets it
  to `QUEUED` and clears `dispatched_at` so it gets redriven — no manual
  intervention, no stuck job, bounded by `DEFAULT_LEASE_SECONDS`.
  `worker-beat` itself running as a single, non-autoscaled replica is safe
  under a rolling update because a second instance briefly overlapping just
  races harmlessly on the same atomic UPDATEs everything else uses.
- **The API commits a Job row and then can't reach the broker** (a
  transient network partition during a rollout, or Redis itself
  restarting). The request still succeeds — see
  `apps/api/src/neuro_api/routes_recordings.py` / `routes_uploads.py`
  `_dispatch_*` helpers — and `worker-beat`'s outbox sweep
  (`find_undispatched_jobs`) redrives it once the broker is reachable
  again. Nothing about the Kubernetes deployment had to compensate for
  this; the outbox pattern in the app already assumes the broker can be
  briefly unavailable.

## Health probes

`livenessProbe`/`readinessProbe` on the API Deployment intentionally hit
different endpoints: `/health/live` never touches the database (so a slow
Postgres cannot cause the kubelet to kill and restart every API replica at
once — a classic self-inflicted outage), while `/health/ready` does check
DB connectivity (and, with `REQUIRE_DEMO_READY`, the seeded sample), so a
replica that genuinely cannot serve traffic is pulled from Service endpoints
without being restarted. The worker's liveness probe execs
`celery inspect ping`; there is no HTTP server to probe. The web
Deployment's probes hit `/` (no dedicated health route exists in the
Next.js app yet — a reasonable proxy, not a precise one).

## What's deliberately not here

- **No egress NetworkPolicy.** `templates/networkpolicy.yaml` restricts
  *ingress* to api/web pods (from the ingress-controller namespace only)
  and denies all ingress to worker pods (they never accept inbound
  connections). It does not restrict egress: api/worker need to reach
  Postgres/Redis/S3 endpoints whose CIDRs are environment-specific and
  unknowable at chart-authoring time. A blind default-deny-egress policy
  templated here would risk silently breaking every real cluster this
  chart is installed into. Tighten egress with your environment's actual
  CIDRs once you have them.
- **No queue-depth-based autoscaling.** The worker HPA scales on CPU
  utilization — the baseline every cluster supports without an extra
  dependency. A KEDA `ScaledObject` reading Celery/Redis queue length
  directly is the more accurate signal for a job worker and is the natural
  next step, but needs KEDA installed as a cluster-level dependency this
  chart does not assume, and could not be validated without a cluster to
  test a `ScaledObject` against.
- **No Grafana dashboard.** `docker-compose.yml`'s `observability` profile
  runs Prometheus scraping `/metrics` (see `docs/OBSERVABILITY.md`); no
  panel JSON is checked in anywhere. Shipping an unverified dashboard is
  worse than shipping none — build one against real metric names once
  there is real traffic to look at.
- **No worker metrics scraping.** Only the API process exposes `/metrics`
  (`neuro_workspace.metrics`); the Celery worker has no HTTP server for
  Prometheus to scrape in this implementation. A `celery-exporter`-style
  sidecar would close this gap and is not added.
