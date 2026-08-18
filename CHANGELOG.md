# Changelog

All notable changes follow Keep a Changelog and semantic versioning principles.

## [Unreleased]

## [0.1.0-alpha.1] - 2026-08-18

### Added

- Zero-account, attributed OpenNeuro public EEG sample with a one-minute guide.
- Returning-user workspace, hosted OIDC authorization-code/PKCE flow, and explicit
  loading, empty, failure, and recovery states.
- Privacy-allowlisted product events, aggregate activation/retention queries, and
  in-app feedback with an operator-only inbox.
- Copyable diagnostic references and safe worker failure context.
- Public-alpha deployment, security, performance, user-testing, outreach, media,
  roadmap-freeze, release, and final-readiness documentation.
- Browser coverage for sample, own-data import/annotation/reopen/export, safe
  failure, and cross-user authorization.

### Existing alpha capabilities

- Workspace-scoped API, direct upload state, background jobs, immutable recordings,
  annotations, bad-channel review, QC, processing, exports, memberships, audit
  events, and coarse workspace metrics.
- EDF/EDF+, BDF, BrainVision, EEGLAB, FIF, configured CSV, and BIDS foundations
  independently exercised with checksum-pinned public data.
- Bounded Canvas waveform viewer, non-root container definitions, migrations, CI,
  scheduled real-data validation, Apache-2.0 license, and contributor/security
  materials.

### Known validation gap

- Docker/Compose execution and restart-persistence were not run because Docker was
  unavailable on the release-preparation host. They remain a required pre-invite
  deployment gate.
