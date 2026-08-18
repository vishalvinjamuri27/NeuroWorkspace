# Deployment and operations

## Environments

Development, test, staging, and production use isolated databases, buckets, queues,
identity clients, encryption keys, analytics, and secrets. Production credentials
are never usable locally. Configuration is validated at startup; dev auth and local
object storage cannot start when `ENVIRONMENT=production`.

## Local setup

Docker Compose runs web, API, worker, PostgreSQL 18, Redis, and MinIO. Containers
have health checks and named volumes. The repository also supports a lightweight
test mode without infrastructure for unit tests. Exact commands live in README and
the alpha report; `.env.example` documents every value without secrets.

## First hosted architecture

- managed container service: independently scaled API and worker containers;
- Next.js container or managed frontend deployment;
- managed PostgreSQL with PITR, Multi-AZ option, TLS, and restricted network;
- managed Redis used as broker/cache, not source of truth;
- private S3-compatible bucket with default encryption, blocked public access,
  lifecycle for temporary uploads/exports, and scoped service identities;
- CDN only for immutable authorized waveform responses when the chosen signed
  delivery design preserves tenant access.

No Kubernetes is required. Terraform is deferred until a specific target is chosen;
checked-in deployment contracts and environment validation prevent fake portable
IaC.

## Release

CI builds immutable, minimal, non-root multistage images labeled with commit and
dependency provenance. Staging runs migrations, smoke/authorization tests, sample
viewer, and one small real file before promotion. Production deploys API/web with
rolling health gates, then workers. Schema changes use expand/migrate/contract;
old and new application versions overlap safely.

Health endpoints separate liveness from readiness. Readiness verifies configuration
and critical dependency reachability without doing expensive work. Workers expose
heartbeat/queue metrics. Startup never auto-runs destructive migrations.

## Backup and restore

- PostgreSQL: daily backups plus PITR WAL retention, encrypted and cross-account/
  region according to deployment risk. Quarterly restore drill to an isolated DB.
- Object store: high-durability class, checksum manifests, optional versioning/
  replication balanced against deletion policy. Inventory detects DB/object drift.
- Redis: no scientific source of truth; rebuild cache and requeue recoverable jobs
  from database state.
- Restore order: infrastructure, database point, object inventory/hash validation,
  reconcile in-flight jobs, start read-only verification, then resume writes.

Migration rollback prefers forward fixes; each release records last compatible
application/schema version. Destructive column removal waits through a release.

## Capacity and cost controls

Workspace upload/storage/concurrency quotas, multipart expiry, queue backpressure,
worker memory classes, request point limits, cache byte limits, export expiry, and
bucket lifecycle prevent unbounded spend. Costs and capacity are measured per
coarse workload; no fabricated cost estimate is part of readiness.

## Production checklist

TLS/HSTS, explicit CORS/origins, secure cookies/OIDC, secret manager, least-privilege
IAM, private storage/database, backup/restore drill, migrations, alerts/runbooks,
retention/deletion policy, terms/privacy/non-clinical notice, dependency/image scan,
real-file smoke, cross-tenant tests, and operator on-call ownership.

