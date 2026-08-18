# Milestone verification reports

These reports record implementation evidence as of 2026-08-18. Commands and full
results are consolidated in `ALPHA_READINESS_REPORT.md`.

## M0 — product and technical specification

- **Implemented:** research review, product/technical/data/ingestion/viewer/security/
  observability/deployment/test specs, threat model, roadmap, and six ADRs.
- **Tests run:** spec consistency review and repository-root/source-boundary check.
- **Real data tested:** dataset candidates, licenses, versions, and fetch strategy
  were researched; no compatibility claim was made in this milestone.
- **Performance observations:** point budgets and target SLOs defined, not measured.
- **Known limitations:** implementation evidence did not yet exist.
- **Architecture deviations:** none.
- **Next milestone:** repository foundation.

## M1 — foundation

- **Implemented:** Python/TypeScript monorepo, lockfiles, FastAPI/Next.js/Celery,
  SQLAlchemy/Alembic, settings, Compose, health boundaries, and typed OpenAPI.
- **Tests run:** fresh migration upgrade/downgrade/upgrade and production web build.
- **Real data tested:** none at foundation.
- **Performance observations:** none.
- **Known limitations:** container runtime was unavailable locally.
- **Architecture deviations:** canonical Zarr uses a shared volume in the first
  deployment; source uploads/exports use S3-compatible storage.
- **Next milestone:** ingestion.

## M2 — ingestion and canonical signal storage

- **Implemented:** safe grouped uploads, format detection/validation, adapters,
  immutable Zarr v3 raw arrays, extrema-preserving pyramids, jobs, checksums, and
  failure cleanup.
- **Tests run:** unit/property/integration tests and all pinned real-format tests.
- **Real data tested:** PhysioNet EDF+, public BDF, OpenNeuro BrainVision/EEGLAB,
  and official MNE FIF.
- **Performance observations:** 32-channel/30-minute generated ingestion measured
  2.099 seconds on the local workstation.
- **Known limitations:** browser uploads are single signed PUTs, not multipart/
  resumable sessions.
- **Architecture deviations:** mixed-rate EDF uses a temporary disk memmap to avoid
  slice-boundary resampling artifacts.
- **Next milestone:** viewer.

## M3 — viewer

- **Implemented:** Canvas rendering, bounded envelope queries, zoom/pan/jump/reset,
  channel virtualization/filtering, scale controls, cursor, prefetch/cache, keyboard
  support, and responsive layout.
- **Tests run:** production build, browser visual QA, two Playwright flows.
- **Real data tested:** API waveform windows for all five pinned binary formats;
  browser acceptance uses deterministic CSV and the public synthetic sample.
- **Performance observations:** generated Scenario A waveform p50 35.723 ms and
  p95 55.989 ms for 40 local 32-channel viewport queries.
- **Known limitations:** Canvas has textual state/controls but not a waveform-equivalent
  screen-reader representation.
- **Architecture deviations:** Canvas 2D was sufficient; no WebGL dependency.
- **Next milestone:** review/QC.

## M4 — review and QC

- **Implemented:** source events, annotation create/edit/delete with optimistic
  revisions, manual bad/good channel state, transparent QC metrics/flags, and
  non-clinical disclaimers.
- **Tests run:** API integration plus browser annotation/bad-channel/QC flow.
- **Real data tested:** QC completed for every pinned format.
- **Performance observations:** QC uses a bounded first-window pass.
- **Known limitations:** thresholds are initial heuristics, not clinically validated.
- **Architecture deviations:** none.
- **Next milestone:** processing/export.

## M5 — processing and export

- **Implemented:** immutable average-reference runs, provenance links, annotation
  CSV, QC JSON, provenance JSON, and authenticated viewer downloads.
- **Tests run:** API integration, browser download, and public-format provenance
  export assertions.
- **Real data tested:** provenance export contains actual format/source hashes for
  every pinned format.
- **Performance observations:** processing is whole-version and not yet benchmarked
  independently.
- **Known limitations:** one processing operation and three export formats.
- **Architecture deviations:** BIDS is validated interoperability foundation, not a
  complete dataset import/export workflow.
- **Next milestone:** collaboration.

## M6 — collaboration

- **Implemented:** workspace/project memberships, owner/admin/member/viewer policy,
  last-owner protection, attribution, cross-tenant existence hiding, audit events,
  and owner/admin aggregate metrics.
- **Tests run:** role, cross-tenant, last-owner, activity, and metrics integration.
- **Real data tested:** authorization is format-independent.
- **Performance observations:** no multi-user load benchmark.
- **Known limitations:** collaborators must sign in once before an owner can add
  them; membership management has API but no dedicated UI.
- **Architecture deviations:** invite tokens/email delivery intentionally deferred.
- **Next milestone:** hardening.

## M7 — hardening

- **Implemented:** threat model, safe errors/logging boundary, CORS/CSRF-origin
  defense, secure cookies, OIDC verification, quotas, deletion cleanup, CI audits,
  scheduled real-data workflow, benchmark harness, and responsive polish.
- **Tests run:** lint, strict types, migrations, 22 Python tests, frontend unit tests,
  browser E2E, build, real data, and 81%+ full-suite coverage.
- **Real data tested:** complete matrix in the readiness report.
- **Performance observations:** only controlled Scenario A and real long-duration
  ingestion; concurrency and slow-storage tests remain open.
- **Known limitations:** no PHI readiness, malware scanner, rate limiter, or completed
  hosted penetration/restore drill.
- **Architecture deviations:** Docker definitions validated statically only because
  Docker is not installed on the workstation.
- **Next milestone:** alpha packaging.

## M8 — open-source alpha package

- **Implemented:** README screenshot/quickstart, Apache-2.0, contributing/security/
  conduct/governance strategy, issue/PR forms, changelog, release workflow, roadmap,
  sample, containers, deployment docs, and this measured report.
- **Tests run:** final commands in the readiness report.
- **Real data tested:** six real-data tests covering five formats plus BIDS.
- **Performance observations:** measured values are published without extrapolation.
- **Known limitations:** no hosted alpha or external-user evidence exists yet.
- **Architecture deviations:** `v0.1.0` is prepared in source; no remote/tag was
  created because no publication target was provided.
- **Next milestone:** recruit external alpha users and prioritize observed pain.
