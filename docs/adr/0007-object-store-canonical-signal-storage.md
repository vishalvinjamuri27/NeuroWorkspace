# ADR 0007: Object-store-backed canonical signal storage

## Context

ADR 0002 chose Zarr for canonical signals and flagged "atomic publication"
and cloud access as open work. The v0.1.0-alpha shipped a stopgap:
`LocalZarrSignalStore` writing to a POSIX path, with every API and worker
replica required to mount the same durable network volume
(`OBJECT_STORE_ROOT/canonical`). That is a real horizontal-scaling
bottleneck: it requires a network filesystem (NFS/EFS-class) as an extra
operational dependency distinct from the S3-compatible object storage
already used for source uploads and exports, and it is the one piece of
this system's state that is not naturally replica-agnostic.

## Decision

Add `S3ZarrSignalStore` (`neuro_workspace.signal_store`), which opens Zarr
groups directly against S3-compatible storage via `zarr.storage.FsspecStore`
backed by `s3fs`, using the same bucket and endpoint already configured for
`S3ObjectStore` (source uploads/exports). `LocalZarrSignalStore` and
`S3ZarrSignalStore` now share one `ZarrSignalStore` base class that owns the
write/pyramid-build/query logic; each subclass supplies only the four
storage primitives that differ (open a Zarr group, read/write a small text
file, check/delete a version). `create_signal_store(settings)` selects the
backend from the same `OBJECT_STORE_BACKEND` setting that already selects
between `LocalObjectStore`/`S3ObjectStore` — there is no new configuration
surface. Compose's default `.env.example` already sets
`OBJECT_STORE_BACKEND=s3`, so the reference deployment now gets this for
free; `docker-compose.yml`'s `canonical-data` shared volume and its mounts
on `api`/`worker`/`worker-beat`/`bootstrap` are removed.

Zarr's chunked layout is what makes this viable rather than merely
possible: a viewport query (`ZarrSignalStore.query`) or a processing read
(`read_raw_window`) only fetches the byte ranges its chunks cover, the same
as the local-path backend — there is no "download the whole recording"
step introduced by moving to HTTP-backed storage.

## Atomicity

An interrupted `write()` (network failure, process kill mid-pyramid-build)
can leave a partial object prefix behind. This is safe by construction, not
because writes are transactional: `services.jobs` only creates the
`RecordingVersion` database row *after* `write()` returns successfully (see
`execute_ingestion_job`/`execute_processing_job`), and every read path
resolves a version through that row. A version_id with no `RecordingVersion`
row is never queried by anything, so a partial prefix is orphaned, not
"live" — the database transaction is the actual publication boundary, not
any object-store operation. A background sweep to garbage-collect orphaned
prefixes (there is no `RecordingVersion` row and the prefix's age exceeds a
grace period) is a reasonable operational addition but is not required for
correctness, and is not yet implemented.

## Alternatives considered

**Keep the shared POSIX volume, document it as an infrastructure
requirement.** Rejected: this is exactly the operational burden object
storage is meant to remove, and cloud environments where a consistent
multi-writer network filesystem is itself the harder dependency to run
reliably than S3-compatible storage.

**A gateway/proxy service in front of local disk, replicated per-replica
with cache invalidation.** Rejected as unjustified complexity — it
reintroduces a stateful service and a cache-coherence problem to solve a
problem S3-compatible storage already solves.

**obstore instead of s3fs/FsspecStore.** obstore (Rust-backed, via
`zarr.storage.ObjectStore`) is a credible, likely-faster alternative and
`zarr` 3.3 supports it directly. `s3fs` was chosen because
`neuro_workspace.storage.S3ObjectStore` already depends on `boto3`/`s3fs`
conventions for source/export storage, keeping one S3 client story instead
of two; this is a reasonable place to revisit if chunk-read latency proves
to be a bottleneck under load (see docs/PERFORMANCE.md).

## Consequences

API and worker replicas are now stateless with respect to canonical signal
data — any replica can serve any version, satisfying real horizontal
scaling for both `api` and `worker` in `docker-compose.yml` via
`--scale`. `LocalZarrSignalStore` remains for single-machine native
development (`make dev`), where a network filesystem would be pure
overhead. The local and S3 code paths are exercised by the same shared
`ZarrSignalStore.write/validate/query` logic (`tests/python/test_signal_store.py`),
and the S3 path additionally has its own opt-in integration suite
(`tests/python/test_s3_signal_store.py`, `make test-s3`) that proves the
no-shared-filesystem property against a real MinIO container: one store
instance writes, a second, independent instance reads back correctly.
