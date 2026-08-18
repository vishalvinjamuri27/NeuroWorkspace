# Public alpha deployment

## Chosen shape

Use a managed container platform without Kubernetes:

```text
browser
  ├─ Next.js web
  └─ FastAPI API ─ PostgreSQL
                ├─ Redis broker ─ Celery worker
                ├─ private S3-compatible source/export bucket
                └─ shared durable POSIX volume for canonical Zarr
```

Web, API, and worker are separate images. API and worker may scale independently
only when every replica mounts the same canonical volume at
`OBJECT_STORE_ROOT/canonical`. The current signal store is not object-native;
deploying API replicas with separate local disks will make recordings intermittently
unreadable. A managed NFS/EFS-class volume is therefore required for hosted alpha.

Start with one web, one API, and one worker replica. Scale only after queue age,
request latency, and storage I/O show which component is constrained.

## Required managed services

- PostgreSQL with TLS, automated backups/PITR, restricted networking, and a
  migration-capable release identity;
- Redis compatible with Celery, private networking, authentication, and no
  scientific source-of-truth role;
- private S3-compatible storage with public access blocked, encryption, versioning
  or an equivalent recovery policy, CORS limited to the web origin, and lifecycle
  rules for temporary uploads/exports;
- a durable shared POSIX network filesystem mounted by every API/worker replica;
- an OIDC provider supporting authorization code, PKCE, discovery, JWKS, and the
  `openid email profile` scopes;
- a secret manager and TLS-enabled custom domains for web and API.

## Configuration

Set these in the secret/configuration system, never in an image:

| Variable | Requirement |
|---|---|
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | PostgreSQL DSN using TLS |
| `REDIS_URL` | authenticated private broker URL |
| `OBJECT_STORE_BACKEND` | `s3` |
| `OBJECT_STORE_ROOT` | shared mounted path, for example `/data/objects` |
| `S3_ENDPOINT_URL`, `S3_BUCKET` | private service endpoint and bucket |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | least-privilege service credentials; prefer workload identity where supported |
| `SESSION_SECRET` | at least 32 random characters, independently rotated |
| `DEV_AUTH_ENABLED` | `false` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | provider values |
| `API_PUBLIC_URL`, `WEB_PUBLIC_URL` | final HTTPS origins |
| `NEXT_PUBLIC_API_URL` | final public API URL ending in `/api`, supplied at web build time |
| `NEXT_PUBLIC_REPOSITORY_URL` | public repository URL, supplied at web build time |
| `CORS_ORIGINS` | exact web origin; no wildcard |
| `ALPHA_ADMIN_EMAILS` | minimal comma-separated operator allowlist |
| `REQUIRE_DEMO_READY` | `true` |
| `WORKER_EAGER` | `false` |
| `MAX_UPLOAD_BYTES` | explicit alpha quota |
| `BUILD_VERSION` | immutable commit/release identifier |

Production validation refuses dev auth, a non-HTTPS issuer, short session secret,
local object storage, missing OIDC values, or a deployment that does not require a
ready sample.

## Build and release order

1. Build the checked-in web, API, and worker Dockerfiles from one commit. Supply
   `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_REPOSITORY_URL` to the web build.
2. Run dependency and image scanning. Record immutable image digests.
3. Provision private networking, database, broker, bucket, and shared volume.
4. Run `uv run alembic upgrade head` as a one-off release job using the new image.
5. Run `uv run python scripts/seed_public_sample.py` as a one-off release job with
   the API/worker storage mounts. It checksum-verifies and ingests the pinned CC0
   OpenNeuro sample and is idempotent once both DB and canonical data are present.
6. Start one worker and confirm broker connection/heartbeat.
7. Start the API. `GET /health/live` must return live; `GET /health/ready` must
   verify the database and canonical sample.
8. Start the web and smoke the landing → sample → waveform/QC path.
9. Smoke OIDC, one small deidentified upload, ingestion, annotation, reopen,
   export, feedback, and a two-account authorization denial.
10. Enable invitations only after monitoring, backups, privacy notice, and an
    identified human operator are active.

Do not run migrations automatically in every API/worker replica. Do not run the
sample seed concurrently in multiple replicas.

## Startup commands

- API: `uvicorn neuro_api.main:app --host 0.0.0.0 --port 8000`
- worker: `celery -A neuro_worker.celery_app:celery_app worker --loglevel=INFO`
- web: `next start` from the production web image
- migration job: `alembic upgrade head`
- sample job: `python scripts/seed_public_sample.py`

The checked-in Compose stack supplies PostgreSQL, Redis, MinIO, named data volumes,
health checks, and the same service separation for local evaluation.

## Health, backup, and recovery

`/health/live` is process liveness. `/health/ready` is the traffic gate. In
production it fails if the DB is unavailable or the published demo row/canonical
version is absent. Monitor API status/latency, oldest queue age, ingestion failures
by safe format, worker restarts/RSS, shared-volume I/O/capacity, database
connections, object errors, and deletion backlog.

Back up PostgreSQL with PITR and run a restore drill. Protect the S3 bucket with
encryption, inventory, and a recovery policy consistent with user deletion. Back
up or snapshot canonical storage; S3 source objects can support reconstruction but
are not an immediate substitute for the active Zarr tree. Redis can be recreated.
Validate database/object/canonical consistency after restore before enabling writes.

## Rollback

Keep the previous three image digests. Migrations must remain backward-compatible
for one release. On a failed rollout, stop new workers, remove the new API/web from
traffic, redeploy the previous images, and leave expanded schema in place. Prefer a
forward migration fix; never reverse a destructive migration against live data.
Reconcile jobs left `QUEUED` or `RUNNING` before resuming workers. If the sample
gate fails, keep readiness red until its exact DB and canonical state is repaired.

## Cost and scaling controls

No dollar estimate is asserted without a selected provider and measured usage.
Early cost drivers are PostgreSQL baseline/backup retention, Redis baseline,
network-volume provisioned throughput and capacity, S3 bytes/requests/egress,
container CPU/RAM during MNE ingestion, retained source/canonical duplication, and
logging/trace volume.

Scaling knobs are API replica count, worker concurrency and memory class, separate
queues for ingest/processing/deletion, upload and workspace quotas, canonical-volume
throughput, PostgreSQL pool size, export/upload lifecycle, and viewer point/channel
limits. The 8-hour generated measurement in `PERFORMANCE_VALIDATION.md` is a
single-process reference, not a cloud capacity guarantee.

## Current validation status

On 2026-08-18, the host had no `docker` command or Compose plugin. Image execution,
Compose boot, service restart, and persistence across a container restart are
**BLOCKED / NOT RUN** here. Python, frontend, migration, browser, public-sample,
clean-clone, and static container checks are recorded separately. Before any public
invite, another engineer must run:

```bash
docker version
docker compose config
docker build -f apps/api/Dockerfile -t neuroworkspace-api:alpha .
docker build -f apps/worker/Dockerfile -t neuroworkspace-worker:alpha .
docker build -f apps/web/Dockerfile -t neuroworkspace-web:alpha .
docker compose up --build -d
docker compose exec api alembic upgrade head
docker compose ps
curl --fail http://localhost:8000/health/ready
```

Then complete the 17-step restart/persistence procedure in
`USER_READY_ALPHA_REPORT.md`. Do not convert this blocked status into “passed”
from static inspection alone.
