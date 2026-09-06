# Adversarial specification review

Reviewed 2026-08-18 before production implementation.

## Problems found and changes made

1. **Scope was not shippable as one undifferentiated V1.** The product workflow is
   now alpha-critical; BIDS dataset import completeness, comments, live streams,
   spectra/topography, ML, and enterprise features are explicit P1/future.
2. **A generic signal abstraction could erase format semantics.** The model now
   keeps source units/rates, discontinuities, companion roles, namespaced BIDS
   metadata, electrodes separate from channels, and immutable originals.
3. **Zarr selection risked becoming a browser contract.** It is private behind a
   signal-store/window interface; manifests version layout and API returns semantic
   envelopes.
4. **WebGL was premature.** Canvas 2D is selected behind a renderer boundary with
   concrete benchmark gates for switching.
5. **Mixed-rate EDF could be silently normalized.** Source channel rates and MNE
   normalization are provenance; mixed-timebase storage remains a known limitation.
6. **Direct upload could bypass authorization/integrity.** Upload creation and
   completion are workspace-scoped, signed per part, size/checksum revalidated, and
   only a completed manifest can create ingestion work.
7. **Parser safety was underdefined.** Untrusted files are processed in constrained
   workers with safe grouping/path rules; archives are disabled for P0.
8. **QC risked medical interpretation and conflation with human decisions.** Rules
   are versioned review suggestions with visible math; confirmed bad state is a
   separate attributed entity.
9. **Auth vendor choice could dominate the architecture.** A small identity
   interface, hosted OIDC adapter, and production ban on dev auth preserve focus.
10. **Real-data checkmarks could be self-referential.** Expected facts and checksums
    come from pinned public sources; reports distinguish parse/metadata/waveform/
    events/QC/export and retain blocked status.
11. **Deletion could race delayed tasks/backups.** Synchronous inaccessibility,
    tombstones, desired-state check before publish, asynchronous object sweep, and
    disclosed backup expiry are required.
12. **Performance targets lacked payload limits.** The API caps points and bytes;
    queries scale with visible channels/pixels, and scenario runs record hardware.

## Gate answers

- Product: one coherent raw-to-review-to-export workflow, plus sample path.
- Scope: broad but staged; alpha blockers are named, future work is not disguised.
- Architecture: modular monolith, managed primitives, no premature Kubernetes.
- Scale: chunked lazy ingest, immutable Zarr, envelopes, bounded viewport API.
- Scientific integrity: explicit time/unit/event/provenance invariants and tests.
- Security/privacy: workspace-scoped access and objects; protected metadata; parser
  isolation; non-clinical/deidentified alpha boundary.
- UX: user-facing states/errors, sample-first flow, professional viewer controls.
- Testing: property/integration/E2E/security/load plus independent real-data suite.
- Extensibility: registries for adapters, QC, exports, processing, future models.

## Unresolved risks

- Actual public dataset candidates/checksums/format details must be verified during
  implementation; candidate presence is not acceptance.
- MNE/Zarr current-version compatibility and object-store performance require an
  executed lock/test matrix.
- Canvas at 256 simultaneously visible channels may miss frame targets.
- Full worker sandboxing depends on deployment runtime controls unavailable to pure
  application tests.
- Dev machine currently lacks Docker, so Compose clean-start verification may be
  blocked unless the runtime becomes available; build/config can still be tested.
- Alpha breadth may still exceed a single release; scientific/security gates win
  over secondary UI polish or format export breadth.

## Decision

Proceed with vertical slices: foundation, one real EDF end to end, viewer, remaining
formats, annotation/QC, processing/export, collaboration/hardening. No material
spec issue requires user input.

