# ADR 0002: Canonical signal storage

## Context

Recordings may contain billions of values. Queries need bounded channel/time
windows, cloud object storage, compression, and immutable derived versions. Raw
files must remain intact.

## Decision

Keep raw source assets immutable and store normalized SI float32 signals in private
Zarr v3 arrays with time-oriented chunks, consolidated/versioned manifests, and
separate min/max pyramid arrays. Hide Zarr behind `SignalStore` and never expose
its object layout as a client contract.

## Alternatives

HDF5/FIF are mature but awkward for direct object-store chunk access. Parquet is
excellent for events but unnatural for dense 2-D arrays. TileDB adds an engine and
operational dependency. Custom binary tiles create a proprietary format/migration
burden.

## Consequences

Python and cloud access are natural and requests avoid full files. Object counts,
shard geometry, float precision, Zarr ecosystem changes, and atomic publication
must be benchmarked and versioned. Original bytes remain the recovery/interchange
authority.

