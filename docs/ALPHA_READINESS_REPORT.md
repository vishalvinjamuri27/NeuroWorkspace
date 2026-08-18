# NeuroWorkspace v0.1.0 alpha-readiness report

Date: 2026-08-18

Status: **source-complete alpha candidate; not production-, clinical-, or PHI-ready.**
All locally executable scientific, API, browser, build, migration, and dependency
checks pass. Container definitions could not be built or executed because Docker is
not installed on the validation workstation. A hosted deployment, restore drill,
native-acquisition BDF file, and external-user trial are still required before an
unqualified “alpha ready” declaration.

## What exists

- Workspace/project organization with owner, admin, member, and viewer roles.
- Signed development sessions and a provider-neutral OIDC verification path.
- Safe grouped uploads for EDF/EDF+, BDF, BrainVision, EEGLAB, FIF, and configured
  CSV/TSV; immutable source objects, SHA-256 provenance, and corrective errors.
- Persistent ingestion/job state, Celery execution, retry policy, migrations, and
  deletion cleanup.
- Immutable Zarr v3 float32 signal versions with extrema-preserving multiresolution
  envelopes and bounded viewport responses.
- Canvas viewer with channel virtualization, filtering, zoom/pan/jump/reset,
  prefetch/cache, scaling controls, cursor, events, annotations, bad-channel review,
  QC, metadata, keyboard controls, and responsive layouts.
- Optimistic annotation revisions, average-reference derived versions, annotation
  CSV/QC JSON/provenance JSON exports, audit events, and operator metrics.
- Deterministic no-human-data sample, public-data fetch manifest, tests, CI,
  containers, Compose, deployment/security/operations docs, and OSS scaffolding.

## Architecture

```text
Browser / Next.js / Canvas
           |
        FastAPI -------------- PostgreSQL metadata and durable job state
           |  \
           |   \-------------- private S3-compatible source/export objects
           |
        Redis/Celery workers -- MNE adapters -- immutable Zarr + pyramids
                                           (shared durable volume in v0.1.0)
```

The local/test path can run SQLite, local object files, and eager workers. Compose
uses PostgreSQL, Redis, MinIO, separate API/worker/web containers, and a shared
canonical-data volume. Production settings refuse dev auth, missing OIDC, a short
session secret, or a non-S3 source object store.

## Supported formats

| Format | v0.1.0 support statement |
|---|---|
| EDF / EDF+ | Full import acceptance on a long mixed-rate signal plus EDF+ hypnogram |
| BioSemi BDF | Public 24-bit BDF derivative accepted; native-acquisition acceptance remains open |
| BrainVision | Grouped `.vhdr/.vmrk/.eeg` import accepted, including 456 markers |
| EEGLAB | Embedded-data `.set` accepted; missing/unsafe external companion paths are validated |
| MNE FIF | Official truncated MNE raw file accepted, including mixed channel types |
| CSV / TSV | Explicit schema wizard, unit conversion, time-column validation, and end-to-end import |
| BIDS EEG | Discovery/read foundation validated on one OpenNeuro subset; not yet a product import mode |

“Accepted” means the exact pinned file parsed, normalized, fully ingested, pyramided,
QC-scanned, queried, and represented in a provenance export. It does not imply every
producer/version/feature of that format is supported.

## Public datasets actually tested

| ID and source | Exact artifact | Size | Provenance |
|---|---|---:|---|
| [PhysioNet Sleep-EDF Expanded 1.0.0](https://physionet.org/content/sleep-edfx/1.0.0/) | `SC4001E0-PSG.edf` + `SC4001EC-Hypnogram.edf` | 48,342,668 B | SHA-256 pinned; ODC-BY 1.0 |
| [HBN-EEG Foundation Challenge](https://eeg2025.github.io/data/) | R1 mini `DiaryOfAWimpyKid` BDF | 4,730,030 B | SHA-256 pinned; CC-BY-SA-4.0 dataset; filtered/downsampled derivative |
| [OpenNeuro ds006547 v1.0.0](https://openneuro.org/datasets/ds006547/versions/1.0.0) | subject 01 BrainVision triplet | 378,067,337 B | SHA-256 pinned; CC0; DOI version pinned |
| [OpenNeuro ds004362 v1.0.0](https://openneuro.org/datasets/ds004362/versions/1.0.0) | subject 001 run 1 EEGLAB + BIDS sidecars | 3,221,757 B | SHA-256 pinned; CC0; DOI version pinned |
| [MNE testing-data](https://github.com/mne-tools/mne-testing-data) | `sample_audvis_trunc_raw.fif` | 12,036,840 B | commit and SHA-256 pinned; fetched, never redistributed |

Public data stays in ignored `.cache/neuro-workspace/testdata`. The repository
contains manifests and fetch logic, not recordings.

## Real-data test matrix

| Format | Real file | Parse | Metadata | Waveform | Events | QC | Export |
|---|---|---:|---:|---:|---:|---:|---:|
| EDF / EDF+ | Sleep-EDF PSG + hypnogram | ✅ | ✅ | ✅ | ✅ annotations | ✅ | ✅ provenance |
| BioSemi BDF | HBN R1 mini derivative | ✅ | ✅ | ✅ | ✅ none present | ✅ | ✅ provenance |
| BrainVision | OpenNeuro ds006547 subject 01 | ✅ | ✅ | ✅ | ✅ 456 | ✅ | ✅ provenance |
| EEGLAB | OpenNeuro ds004362 subject 001 run 1 | ✅ | ✅ | ✅ | ✅ 1 | ✅ | ✅ provenance |
| FIF | MNE `sample_audvis_trunc_raw.fif` | ✅ | ✅ | ✅ | ✅ none present | ✅ | ✅ provenance |

BIDS acceptance separately passed subject/run discovery, dataset description,
participants metadata, channel/electrode/coordinate sidecars, events, and MNE-BIDS
reading for the ds004362 subset.

## Automated test counts

Final local run:

- Python: **22 passed** in 50.18 s, including 6 public-real-data tests.
- Python branch-aware coverage: **82.31%** overall; enforced floor 80%.
- Frontend unit: **2 passed**.
- Chromium E2E: **2 passed** in 3.9 s. The complete flow covers development sign-in,
  CSV upload/ingestion, viewer load, zoom, pan, annotation create/edit/delete,
  bad-channel marking, QC, and authenticated export download.
- Strict Python and TypeScript type checks: passed.
- Python and frontend lint/format checks: passed.
- Next.js optimized production build: passed.
- Fresh Alembic migration and schema check: passed.
- Python dependency audit: no known vulnerabilities.
- pnpm audit after upgrading Vitest to 3.2.6: no known vulnerabilities.
- Docker image build/runtime: **BLOCKED — `docker` executable is absent**.

The only test warnings are an upstream Starlette `TestClient` transition notice and
the deliberately visible Sleep-EDF hypnogram warning described below.

## Performance results

Measured on macOS 15.1, Apple M4 Pro, 24 GiB RAM, arm64. These are local product
checks, not cross-machine claims.

Generated Scenario A: 32 channels × 30 minutes × 256 Hz (14,745,600 channel-samples,
58,982,400 logical float32 bytes), deterministic signal/noise plus one 500 µV
extreme.

| Measure | Result |
|---|---:|
| Canonical ingestion + 10 pyramid levels | 2.099 s |
| Effective channel-samples/s | 7,026,634 |
| Observed process peak-RSS increase | 224,362,496 B |
| Canonical on-disk bytes | 88,472,828 B |
| Storage / logical float32 raw | 1.5000× |
| 40 local 32-channel/10-second queries p50 | 35.723 ms |
| Query p95 | 55.989 ms |
| Response point-budget violations | 0 |

The 22-hour Sleep-EDF recording (7,950,000 samples/channel at the normalized
100 Hz timebase) also completed full ingestion, long-window pyramid query, and QC.
Browser render time, HTTP upload throughput, concurrent query p95, concurrent worker
throughput, and slow/remote storage behavior were **not measured** and are not
inferred from these numbers. Scenarios B-D remain future capacity tests.

## Known scientific limitations

- Sleep-EDF’s hypnogram contains one annotation extending outside the PSG range;
  MNE clips it and emits a warning. NeuroWorkspace does not hide this discrepancy.
- Mixed-rate EDF/BDF data are preloaded to a temporary disk-backed MNE memmap before
  chunking. This bounds RAM and prevents lazy-slice resampling seams, but needs free
  temporary disk roughly proportional to the normalized recording.
- The HBN BDF is a public filtered/downsampled BDF derivative, not a file directly
  emitted by BioSemi acquisition hardware. It validates BDF decoding and the real
  signal pipeline, but native-acquisition fidelity remains unproven.
- OpenNeuro ds004362 v1.0.0 declares all 64 channel types as `n/a`; MNE-BIDS maps
  them to `misc`. The acceptance test asserts the source defect rather than silently
  relabeling them.
- QC examines a bounded initial window with transparent v1 heuristics. It is not a
  clinical assessment, artifact-rejection algorithm, or validation of data quality.
- A single global normalized timebase is used per version; original per-channel
  sampling rates remain metadata. EDF+D discontinuities and every vendor extension
  do not yet have dedicated acceptance files.
- Processing currently supports only selected-channel average reference.

## Known product limitations

- Direct object uploads use a single signed PUT. True multipart/resume, pause,
  reconnect, and abandoned-part cleanup are not implemented.
- BIDS is a tested interoperability foundation, not a directory-upload/discovery UI.
- Membership management and aggregate metrics are API features without dedicated UI.
- No notification/invite email, comments, saved viewer layouts, montage editor,
  event editing, batch import, or full recording browser exists.
- Canonical Zarr is on a shared filesystem volume in v0.1.0. Source and export
  objects use S3-compatible storage.
- No hosted demo, telemetry vendor, user cohort, adoption evidence, or support SLA
  exists. Product metrics are privacy-safe workspace queries, not claimed usage.
- Container execution, service restart persistence, and clean Compose onboarding
  remain unverified on this machine because Docker is absent.

## Known security/privacy limitations

- Do not upload PHI or identifiable subject data. The project makes no HIPAA claim.
- OIDC signature/audience/issuer verification exists, but no provider-specific login
  UI, MFA policy, SCIM, SSO administration, session revocation store, or recovery flow.
- Audit coverage includes membership changes and viewer opens; it is not complete,
  tamper-evident, or exportable.
- There is no rate limiter, WAF, malware scanner, content-disarm sandbox, formal pen
  test, secret-manager integration test, or tenant-isolated object-store policy test.
- Signed URL/CORS behavior is configured for local MinIO but not runtime-tested here.
- Backups, PITR, restore, incident response, deletion SLA, and retention are designed
  but have not undergone an operator drill.

## What would be required before handling PHI

Complete a formal risk analysis and legal/compliance review; execute BAAs with every
relevant processor; establish documented access, workforce, incident, retention,
breach, and change-control procedures; enforce MFA/SSO and lifecycle management;
complete encryption/key-management and least-privilege reviews; implement complete
tamper-evident audit coverage; validate backups/restores and deletion; perform a
penetration test and dependency/container/SBOM program; add monitoring/on-call and
evidence collection; and validate the selected deployment against the current HIPAA
Security and Privacy Rules. See `HIPAA_GAP_ANALYSIS.md`.

## Largest scalability risks

1. Shared-filesystem canonical Zarr limits horizontal worker/API placement.
2. Pyramid creation is eager and doubles substantial write/I/O work.
3. Temporary normalized memmaps can exhaust local worker disk on long mixed-rate
   files or high concurrency.
4. Single-PUT uploads and in-process local waveform cache are not robust at scale.
5. Job claiming has durable state and idempotency but lacks a fully tested distributed
   lease/reaper under concurrent worker failure.
6. No measured concurrent waveform, ingestion, database, or remote-object load.

## Technical debt

- Replace the deprecated Starlette/httpx1 test client once the ecosystem migration
  path is stable.
- Implement object-native canonical Zarr with atomic publication and inventory.
- Add multipart uploads, quota enforcement, job leases/reaper, cancellation UI, and
  orphan reconciliation.
- Expand audit coverage and unit tests for S3 operations/error paths.
- Add more producer/version fixtures, native BioSemi data, EDF+D, EEGLAB `.fdt`,
  montage/reference edge cases, and BIDS product ingestion.
- Add browser screenshot baselines only after cross-platform rendering is stable.

## Recommended next engineering work

1. Run and repair the Compose stack on Linux, then add a CI Compose smoke covering
   MinIO signed upload, worker ingestion, restart persistence, and deletion.
2. Implement multipart/resumable upload and object-native canonical storage.
3. Test an openly licensed native BioSemi acquisition and add EDF+D/EEGLAB-FDT cases.
4. Add distributed job leases/recovery and concurrent/slow-storage load tests.
5. Add workspace/member UI and a recording/project browser before more processing.

## Recommended next user-discovery questions

- Which first workflow hurts most: upload reliability, overnight navigation,
  annotation handoff, QC triage, or export into an existing analysis stack?
- Which exact acquisition systems, file producer versions, channel counts, durations,
  and BIDS conventions appear in users’ current data?
- What must a collaborator see/change, and how are disagreements resolved today?
- Which exports are required to avoid lock-in, and what provenance must accompany them?
- What response time is acceptable for the common viewport and largest recording?
- Are users evaluating locally, on institutional infrastructure, or in a hosted tool;
  what data-governance constraints decide that choice?

## Exact commands to run the system

Docker path, after Docker is installed:

```bash
cp .env.example .env
docker compose up --build
```

Open <http://localhost:3000>. For native development:

```bash
make install
make migrate
make seed
uv run uvicorn neuro_api.main:app --reload --port 8000
pnpm dev
uv run celery -A neuro_worker.celery_app:celery_app worker --loglevel=INFO
```

## Exact commands to rerun real-data validation

```bash
make lint
make typecheck
make test
make test-e2e
make build
make real-data
make coverage
make benchmark
uv export --frozen --no-dev --no-emit-project --quiet | uvx pip-audit --strict -r /dev/stdin
pnpm audit --audit-level high
```

The real-data fetch commands are checksum/size verified, resumable, and never place
dataset files under Git tracking. `make coverage` expects `make real-data` to have
populated the ignored cache.
