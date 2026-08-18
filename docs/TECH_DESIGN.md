# Technical design

## Architecture

V1 is a modular monolith with separately runnable processes:

```text
Next.js web -> FastAPI API -> PostgreSQL
                    |       -> S3/MinIO object store
                    +------ -> Redis/Celery -> Python worker -> MNE/Zarr
```

The API owns authentication, authorization, metadata transactions, upload
contracts, waveform responses, and job status. Workers own untrusted scientific
parsing, canonical conversion, QC, pyramids, processing, export, and deletion.
The same Python package holds domain/use-case code; framework routes and task
wrappers are thin adapters. In-process execution is test/dev-only.

## Repository

```text
apps/web                  Next.js client and Canvas viewer
apps/api                  API entrypoint and container
apps/worker               worker entrypoint and container
packages/signal-core      Python domain, adapters, storage, QC, processing
packages/shared-types     generated OpenAPI TypeScript types
docs, infra, scripts, tests, testdata
```

Python uses a root `pyproject.toml` and uv lock. pnpm manages the frontend
workspace. Production containers are non-root and multistage. Local Compose runs
web, API, worker, PostgreSQL, Redis, and MinIO.

## Domain and module boundaries

- `domain`: entities, enums, policies, error types; no framework imports.
- `adapters`: RecordingAdapter registry and MNE/CSV implementations.
- `signal_store`: immutable versioned Zarr repositories and envelope pyramids.
- `qc`: pure metric functions plus a rule registry.
- `processing`: validated operations producing new recording versions.
- `services`: use cases with authorization, transactions, object storage, jobs.
- `api`: typed schemas, dependencies, routes, exception mapping.
- `tasks`: idempotent task orchestration and safe diagnostics.

Extension registries make one adapter/rule/export/operation plus tests the normal
addition path. Model plugins later receive a read-only `SignalWindowProvider` and
return versioned candidates, never direct database/storage access.

## State and scaling

API and web processes are stateless and scale horizontally. Workers scale by queue
and resource class; one recording/version owns a conversion lease. PostgreSQL,
Redis, and object storage are stateful managed services in hosted environments.
Scientific bytes never enter database rows.

Stage 1 uses one API and one worker with Postgres/Redis/MinIO. Stage 2 adds API
replicas, autoscaled format/resource queues, CDN/API caching for immutable tiles,
read replicas for admin/reporting, and storage lifecycle rules. Stage 3 may split
ingestion or viewer services only after traces show a boundary, add regional
storage/retention controls, and partition audit/job tables.

At 10x, worker throughput and object count likely fail first. Mitigations are
format queues, shard tuning, bounded concurrency, and backpressure. At 100x,
waveform egress/cache hit rate and metadata/job table contention dominate;
immutable CDN-friendly tile responses, table partitioning, and workload isolation
are the planned boundaries.

## API compatibility

Resources live below `/api`; a version header and generated OpenAPI schema detect
breaking changes before a URL major version is needed. IDs are UUIDv7/UUID4-style
opaque strings. Lists paginate with opaque cursors. Writes accept idempotency keys
where retries create resources. Annotations use a version/ETag precondition.

Job progress initially uses bounded polling with `Retry-After`; SSE is added only
when polling load or UX is measured problematic. Upload data flows via expiring
signed multipart URLs. Local storage exposes an equivalent part endpoint only for
development.

Core resources: sessions, workspaces/members, projects, uploads, recordings,
channels, waveform, events, annotations, bad channels, QC, processing runs,
exports, jobs, and deletion requests. FastAPI generates the explicit OpenAPI file;
CI checks generated TypeScript types for drift.

## Storage and waveform algorithm

Each immutable recording version has:

```text
metadata.json
signals.zarr/raw            float32 [channel, sample]
signals.zarr/envelopes/Lk   float32 [channel, bin, min|max]
events.parquet-or-json
manifest.json               hashes, shapes, units, software provenance
```

Time chunks target 8-32 MiB compressed reads and are tuned with benchmarks. Large
channel counts may use channel shards while preserving small visible-channel
reads. No mutable cache shares a key across versions.

Level zero source values are partitioned into adjacent bins of four samples. For
level `k`, bin `j` covers source indices
`[j*4^(k+1), min((j+1)*4^(k+1), N))`; stored values are exact finite min and max,
plus an all-nonfinite marker. The next level combines four previous bins using
minimum-of-minima and maximum-of-maxima. Therefore every finite source extremum is
represented at every covering level. Property tests prove coverage, boundary,
NaN, monotonic range, and slice equivalence.

Given requested duration, channels, and pixel width `W`, choose the finest level
with bins `<= 1.5W` (or raw if already bounded), enforce hard response limits, and
return bin start/end times with minima/maxima. Query work is proportional to
visible channels and viewport bins, not recording duration.

## Jobs, concurrency, and failure

Jobs have type, entity/version, status, attempt, progress, safe error, internal
diagnostic reference, timestamps, and idempotency key. Task code acquires a DB
lease, rechecks desired state, writes outputs under a temporary version prefix,
validates a manifest, atomically publishes the DB pointer, then removes temporary
objects. Redelivery is safe. Exponential retries apply only to transient failures.

Deletion marks a recording inaccessible synchronously, cancels eligible jobs,
then deletes source, canonical, derived, tile, export, and cache prefixes. A
tombstone prevents resurrection by delayed jobs. Annotation edits use optimistic
versions. Processing cannot start on deleting or unready inputs.

## Authentication and authorization

Core code depends on an `Identity` interface. Local development exchanges a known
email for a signed, HTTP-only, same-site session. Hosted deployments plug in an
OIDC verifier without changing domain policies. Password storage is intentionally
not invented in V1.

Every resource lookup joins through active membership; repositories never expose a
bare `get(id)` to routes. Object keys and signed URLs are issued only after the
same policy check. Role capabilities are centralized and tested as a matrix.

## Observability and privacy

Structured logs carry request/job/recording opaque IDs and timing but exclude
filenames where sensitive, header fields, samples, annotation text, query values,
and signed URLs. OpenTelemetry-compatible traces span API enqueue, worker stages,
object I/O, and waveform queries. Metrics use bounded labels only.

## Deployment and recovery

The first hosted deployment uses managed containers, Postgres, Redis, and S3;
frontend may share container hosting. Database point-in-time recovery, bucket
versioning where compatible with deletion policy, restore drills, migration
expand/contract discipline, and environment-isolated credentials are required.
Kubernetes is not part of V1.

