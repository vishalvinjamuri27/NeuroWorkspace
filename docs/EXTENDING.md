# Extending NeuroWorkspace

Keep scientific code independent of FastAPI, Celery, and React. Every extension
must preserve units, time semantics, source hashes, software versions, and bounded
resource behavior.

## File format

Implement the `RecordingAdapter` protocol in `packages/signal-core`. Detection is
structural and may be ambiguous; validation produces safe, corrective errors;
metadata normalizes names/types/units while retaining source values; chunk
iteration must be contiguous and bounded. Register one adapter in the registry.
Add malformed fixtures, unit/property tests, and a checksum-pinned independently
produced public file. A generated round trip is supporting evidence only.

## QC rule

Add a pure calculation in `qc.py`. Document formula, input unit, threshold,
coverage window, version, limitations, and failure semantics. Automated flags are
review suggestions and never mutate human bad-channel decisions. Add boundary,
NaN, constant-signal, and known-frequency tests.

## Export

Add a stable format identifier and serializer in `exports.py`, then connect it in
the API route and generated OpenAPI types. State losses explicitly, escape
spreadsheet formula prefixes, keep provenance, and test authorization plus a
downloaded artifact.

## Processing operation

Create a pure, versioned signal-core operation. Inputs and parameters are immutable;
outputs are new recording versions with parent links and software provenance. Add
idempotent job handling, cancellation/deletion behavior, and numerical tolerance
tests.

## Experimental behavior

`FEATURE_FLAGS` is a comma-separated, default-off list exposed through validated
settings (`feature_enabled`). Flags are deployment configuration, not authorization;
all flagged paths still require the normal workspace capability checks.
