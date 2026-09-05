# ADR 0002: Canonical signal storage

> The object-storage/atomic-publication questions this ADR left open are
> resolved in [ADR 0007](0007-object-store-canonical-signal-storage.md).
> The "bounded channel/time windows" claim below was verified empirically,
> not just architecturally, in [docs/PERFORMANCE.md](../PERFORMANCE.md)'s
> "Viewport performance" section: an identical viewport request against
> 30-second, 5-minute, and 30-minute recordings (same channel count/rate)
> returned in the same ~7-8ms once warm — flat, not scaling with total
> recording duration.

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

