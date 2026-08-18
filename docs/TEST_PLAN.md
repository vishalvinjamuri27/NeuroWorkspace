# Test plan

## Principles

Scientific correctness, tenant isolation, immutable provenance, and bounded data
movement are release gates. Synthetic tests are necessary but cannot establish
format compatibility. Every reported pass links to an executed command/artifact;
blocked validations stay blocked.

## Fast pull-request suite

### Python unit and property tests

- adapter detection, companion grouping, safe paths, and errors;
- metadata/unit/channel normalization and event/annotation conversion;
- time/seconds/sample indexing including final partial chunks;
- min/max pyramid against direct reference with extrema at every boundary, NaNs,
  infinities, empty/one-sample windows, and randomized arrays;
- QC definitions, thresholds, units, and known synthetic signals;
- processing parameter validation and provenance canonicalization;
- state machines, idempotency, optimistic annotation edits, and deletion races;
- role/action matrix plus guessed-ID isolation for every resource class;
- exports, spreadsheet-injection safety, hashes, and conversion disclosures.

Hypothesis/property generation is deterministic and bounded in normal CI.

### API integration

With an ephemeral database/object store and eager jobs:

```text
upload -> complete -> ingest -> READY -> metadata/waveform -> annotation/bad channel
-> QC -> processing run -> derived version -> export -> delete -> objects absent
```

Tests include retry/redelivery, failure messages, job polling, range/point limits,
ETag, pagination, stale annotation revision, and user A versus user B.

### Frontend

Component tests cover view-state math, API decoding, renderer transforms, channel
selection/virtualization, scale display, CSV wizard validation, and accessible
names. Playwright smoke tests cover sample open, waveform draw, pan/zoom/jump,
annotation CRUD, bad channel, QC, processing, export, and keyboard navigation.
Critical viewer states get stable screenshot baselines after fonts/viewport lock.

### Static/build

Ruff format/lint, mypy strict core modules, pytest, migration head check, ESLint,
TypeScript strict check, frontend tests, production build, OpenAPI/generated-type
drift, dependency audit, secret scan, and container build.

## Real-data suite

Manual/nightly tests fetch pinned public samples from `testdata/manifest.yaml`,
verify SHA-256 before parsing, and run format detection, expected metadata with
tolerances, multiple windows including chunk boundaries/end, events, QC, canonical
round trip, and supported export. BrainVision and EEGLAB tests prove real companion
relationships. BDF must be independently sourced—not generated from EDF.

Results update the alpha report matrix only from the generated JSON report. A
dataset access/license/checksum change is a blocked result, never an inferred pass.

## Scientific assertions

- `duration = n_times / sfreq` under the normalized continuous grid, with source
  discontinuities separately represented;
- returned sample/time intervals follow half-open `[start, stop)` semantics;
- physical values agree with the lazy source reader within format/float tolerance;
- source unit, canonical unit, and conversion factor compose correctly;
- channel order/name/type and bad state are stable across views/exports;
- event/annotation onsets and durations survive ingestion/export within tolerance;
- every finite extremum belongs to and appears in its pyramid bin;
- transformations record parameters/software and never modify source hashes.

## Security and failure tests

Adversarial filenames, companion references, malformed/truncated headers, enormous
declared sizes, duplicate names, invalid encodings, formula cells, XSS payloads,
expired signatures, missing membership, stale/deleted job publication, worker
termination, object I/O error, and DB retry are mandatory. Future archive support
adds zip traversal, symlink, file-count, compression-ratio, and expansion limits.

## Load and performance

Generated streaming stores model:

| Scenario | Channels | Duration | Rate |
|---|---:|---:|---:|
| A | 32 | 30 min | 256 Hz |
| B | 64 | 2 h | 512 Hz |
| C | 128 | 8 h | 512 Hz |
| D | 256 | 2 h | 1024 Hz |

Measure ingest wall time/peak RSS/storage ratio, tile generation, waveform p50/p95
and response bytes under concurrency, worker throughput/retry, browser request/
decode/draw time, long tasks, and heap after repeated interactions. Scenario C/D
may run scheduled on provisioned hardware. Record hardware/configuration and never
extrapolate unrun results.

## Acceptance sequence

From a clean checkout: dependency install, services, migrations from zero, sample,
real EDF/BrainVision/EEGLAB/BDF/FIF, configured CSV, BIDS subset, viewer actions,
processing/export, unauthorized user denial, restart persistence, deletion cleanup,
production builds, and all suites. Exact commands appear in the alpha report.

