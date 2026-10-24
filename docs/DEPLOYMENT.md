# Deployment

Two deployment paths exist: `docker-compose.yml` for a single machine, and
a Helm chart for Kubernetes. Both are covered here. The Compose path is
exercised end to end by the `compose` job in CI (clean build, restart
persistence, worker loss and queue survival, deletion cleanup, and the
Playwright suite against the running stack).

## Local Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

This starts PostgreSQL, Redis, MinIO, a one-shot `bootstrap` container
(runs migrations, then seeds the sample recording), the API, worker,
worker-beat, and the web app, with health checks gating startup order.
`docker compose down -v` stops everything and removes volumes; `docker
compose down` alone keeps data.

## Configuration

Every setting is documented with a safe placeholder in `.env.example`,
grouped by concern (database/broker/storage, sessions/identity, public
origins, resource limits). `ENVIRONMENT=production` refuses to start with
development conveniences enabled (dev auth, local filesystem object
storage) — configuration is meant to fail closed, not silently run with
insecure defaults in a real deployment.

## Database migrations

Alembic is authoritative for schema state; nothing in this application
calls `create_all()` as a substitute. The Compose `bootstrap` container and
the Helm chart's pre-install/pre-upgrade hook Job both run `alembic upgrade
head` before the application starts.

```bash
DATABASE_URL=... uv run alembic current   # what revision the DB is actually at
DATABASE_URL=... uv run alembic upgrade head
```

## Object storage

`OBJECT_STORE_BACKEND=s3` (the default, and the only backend used in
Compose/Kubernetes) targets an S3-compatible endpoint — MinIO locally, a
real bucket in a hosted deployment. Bucket existence is checked and created
on startup with bounded retry/backoff, so a transient connectivity blip at
container startup doesn't crash the process. `OBJECT_STORE_BACKEND=local`
(a plain filesystem path) exists only for native development without
Docker and is not used by either deployment path above.

## Worker configuration

`WORKER_EAGER=false` (the default in both deployment paths) runs real
asynchronous Celery dispatch; `WORKER_EAGER=true` runs tasks in-process
and is a test/native-dev-only shortcut. Each worker process builds its own
database engine and object-storage client *after* Celery's prefork forks
child processes (`worker_process_init` in `celery_app.py`) — building them
before the fork is not fork-safe with the S3 client library used here, a
real bug found and fixed during validation.

## Kubernetes

The Helm chart at [`deploy/helm/neuroworkspace`](../deploy/helm/neuroworkspace)
is the Kubernetes deployment path. **It has been validated on a local
`kind` cluster; it has not been operated on a production-grade cluster
(EKS/GKE/AKS) or against externally-provisioned Postgres/Redis/S3 end to
end.** Validation covered install, upgrade, and API/worker scaling at
1/2/3 replicas.

```text
Ingress (nginx/cert-manager)
  ├─ api.example.com   → Service nw-api   → Deployment nw-api   (HPA 2-10, CPU 70%)
  └─ app.example.com   → Service nw-web   → Deployment nw-web   (HPA 2-8,  CPU 75%)

Deployment nw-worker       (HPA 2-20, CPU 75%)  — Celery worker, no ingress
Deployment nw-worker-beat  (fixed at 1)         — outbox dispatcher / lease-reclaim sweep
Job nw-migrate             (pre-install/pre-upgrade hook) — alembic upgrade head

External (not in this chart): PostgreSQL, Redis, S3-compatible bucket
```

`values.yaml` defaults to external PostgreSQL/Redis/S3
(`devDependencies.enabled: false`) — running a database of record as a
Helm-bundled in-cluster StatefulSet means reinventing backup/PITR, failover,
and storage-class correctness that a managed service already solves.
`values-dev.yaml` layers on single-replica, `emptyDir`-backed dependency
Deployments so the chart is still runnable end to end on a local
`kind`/`minikube` cluster:

```bash
helm install nw deploy/helm/neuroworkspace \
  -f deploy/helm/neuroworkspace/values.yaml \
  -f deploy/helm/neuroworkspace/values-dev.yaml \
  --set image.registry=localhost:5001/neuroworkspace
```

**Secrets**: `secrets.create: false` by default — the chart expects a
Secret named `neuroworkspace-secrets` to already exist, created out of band
(`kubectl create secret generic`, sealed-secrets/SOPS, or an External
Secrets Operator). `values-dev.yaml` sets `secrets.create: true` from
plaintext values for local convenience only — those values are recoverable
from Helm's release history, so never do that against a real cluster.

**Health probes**: `/health/live` never touches the database, so a slow
Postgres cannot cause the kubelet to kill and restart every API replica at
once; `/health/ready` does check DB connectivity, so a replica that
genuinely cannot serve traffic is pulled from Service endpoints without
being restarted. The worker's liveness probe execs `celery inspect ping`.

**Job system continuity under normal Kubernetes churn** — this follows
directly from the job-system correctness work in `services.jobs`, not from
anything Kubernetes-specific: a worker pod killed mid-job (rolling deploy,
node drain, OOM) leaves its lease to expire and `worker-beat` redrives the
job; the API committing a `Job` row while the broker is briefly unreachable
(a rollout, Redis restarting) still succeeds and gets redriven by the same
outbox sweep once the broker returns.

**What's deliberately not in the chart**: no egress `NetworkPolicy` (the
required Postgres/Redis/S3 CIDRs are environment-specific and unknowable at
chart-authoring time — tighten this with your environment's actual CIDRs);
no queue-depth-based autoscaling (the worker HPA scales on CPU, which is
the baseline every cluster supports without an extra dependency; a KEDA
`ScaledObject` reading queue length would be more accurate but needs KEDA
installed); no Grafana dashboard (only Prometheus scraping is wired up —
see [Observability](#observability)); no worker metrics scraping (only
the API process exposes `/metrics`).

## Scaling API and worker replicas

Both are stateless and horizontally replicable (verified live at 1/2/3
replicas on Compose and on `kind`). The one connection-budget
constraint to know before scaling up: each API process holds up to 30
Postgres connections (`pool_size=10 + max_overflow=20`); against Postgres's
default `max_connections=100`, that's roughly 3 replicas before headroom
runs out, and the chart's API HPA ceiling of 10 replicas would need either
a larger `max_connections`, a smaller per-replica pool, or a connection
pooler (e.g. PgBouncer) in front of Postgres — none of which this chart
implements, since no benchmark reached that many replicas. See
[PERFORMANCE.md](PERFORMANCE.md) for the measurement this is based on.

## Observability

The API exposes Prometheus metrics at `GET /metrics` (`neuro_workspace.metrics`):
request counts and latency by route template, job started/succeeded/failed
counts by kind, lease reclaims, dead-letters and redispatches from the outbox
sweep, and cache hit/miss/error counts. `docker compose --profile
observability up` adds a Prometheus instance scraping it
(`observability/prometheus.yml`). Logs are structured JSON via `structlog`
with correlation IDs and safe error codes; signal values, filenames,
annotation text, and signed URLs are never logged. Not implemented: worker
metrics (Celery has no HTTP endpoint here), tracing, dashboards, and alerting.

## Backups

Two stores need backing up: **PostgreSQL** (all metadata, lineage,
authorization, and job state) and the **S3-compatible object store** (every
byte of actual signal data — uploads, canonical Zarr arrays, exports). Use
your platform's standard tooling for each (`pg_dump`/continuous WAL
archiving for Postgres; bucket versioning/replication or `mc mirror` for
object storage) — no backup automation ships with this repository. A
`RecordingVersion` row and its object-storage prefix are two halves of one
fact, so back up and restore them together; if the two backups are taken at
noticeably different times, prefer taking the object-storage backup *after*
the Postgres backup (the opposite order can leave a restored database
pointing at objects that don't exist yet in the object-storage backup).
Redis holds nothing durable — a lost Redis is rebuilt from Postgres state
with no data loss (see [ARCHITECTURE.md](ARCHITECTURE.md#job-dispatch-and-recovery)).

## Current deployment limitations

- No production-grade cluster has been used — only a local `kind` cluster.
- The chart's externally-provisioned Postgres/Redis/S3 path has not been
  exercised end to end (only the dev overlay's bundled dependencies have).
- No backup/restore has actually been executed against a real deployment;
  the procedure above is documented, not automated or rehearsed.
- The API's HPA autoscaling cycle has not been exercised under live load —
  replica *count* changes were verified stateless, but triggering a
  scale-up/down via real CPU load was not.
- No formal external security audit has been performed.
