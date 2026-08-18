# Product specification

## Product boundary

NeuroWorkspace is a non-clinical workspace for turning raw neural or physiological
recordings into understandable, annotated, quality-reviewed, provenance-rich,
analysis-ready artifacts. V1 centers EEG while its domain language uses signals,
channels, recordings, events, and timebases so EMG/ECG/EOG/PPG can follow.

It does not diagnose, recommend care, replace scientific libraries, or silently
transform data. QC outputs are review heuristics. The hosted alpha accepts public,
synthetic, or appropriately deidentified research data only.

## Users and jobs

Primary users are neuroscience researchers and graduate students, EEG/BCI
researchers, neuroengineers, and small neurotechnology teams. Secondary users are
commercial R&D, CRO, digital-biomarker, and other physiological-signal teams.

The first coherent workflow is:

1. Open a public sample or create a workspace/project.
2. Upload a supported recording with visible progress and actionable validation.
3. Inspect normalized metadata and a responsive multichannel waveform.
4. Navigate time/channels, change scale, inspect events, and select regions.
5. Create attributed annotations and confirm bad channels independently of QC.
6. Review transparent QC metrics/flags.
7. preview transformations, persist an immutable processing run, and export.
8. Return later or share within the workspace without losing state/provenance.

This satisfies the seven mission JTBDs: code-free inspection, obvious-quality
review, annotation, metadata understanding, nondestructive processing, internal
collaboration, and interoperable export.

## V1 functional requirements

- Account/session boundary with dev auth and vendor-neutral hosted integration.
- Workspaces with owner/admin/member/viewer roles and projects.
- Sample recording available without upload or signup in local/demo deployments.
- Multipart direct-to-object-storage contract; local fallback uses the same upload
  lifecycle. Browser tab lifetime must not govern ingestion.
- P0 imports: EDF/EDF+, BDF, BrainVision group, EEGLAB group, FIF, and configured
  delimited text. The CSV wizard requires sample rate or a validated time column,
  explicit channels, units, and optional channel types.
- Recording overview: duration, rate/timebase, channels, types/units, format,
  events/annotations, safe start-time presentation, montage availability,
  provenance, processing state, and QC summary.
- Viewport waveform queries and Canvas viewer with pan/zoom/jump/cursor, channel
  virtualization, visibility/groups, scale controls, keyboard navigation, event,
  annotation, bad-channel, and QC overlays.
- User annotations are create/edit/delete with optimistic concurrency, attribution,
  and audit events. Human bad-channel state is distinct from algorithmic flags.
- QC V1: missing/nonfinite, flatline, clipping-like repeated extrema, high absolute
  amplitude, variance outlier, discontinuity, dropout, and line-noise ratio where
  sample rate/window allows. Definitions and thresholds are visible.
- Preview versus persistent processing runs for filtering, notch, re-reference,
  resampling, and channel exclusion. No preprocessing is implicit.
- Annotation CSV/TSV/JSON, QC JSON/report, provenance JSON, FIF where supported,
  and BIDS-oriented export foundations with conversion-loss disclosure.
- Asynchronous deletion removes all scientific objects and cache entries while
  retaining a minimal non-sensitive audit tombstone under documented policy.

## P1 extension points, not alpha blockers

GDF, CNT, XDF, EGI, complete zipped-BIDS import, device adapters, comments,
read-only links, spectra/topography, live acquisition, video, enterprise identity,
and model plugins. The plugin interfaces exist; feature implementations do not.

## UX and accessibility

The application is dense, calm, and research-oriented. First use presents only
`Upload recording` and `Try sample recording` as primary actions. User-facing
states are Uploading, Inspecting, Processing, Ready, Failed, and Deleting—not queue
jargon. Errors state what the user can do next.

All non-waveform controls are keyboard and screen-reader operable, focus is
visible, contrast meets WCAG AA targets, and reduced motion is respected. The
waveform exposes a textual window/channel/scale summary and equivalent navigation
controls. Shortcuts never trap focus.

## Success and measurable targets

Alpha success requires the full workflow without Python edits, independently
verified real EDF/BDF/BrainVision/EEGLAB/FIF inputs, a configured CSV, a BIDS
subset, green fast CI, authorization isolation, persistence after restart,
deletion cleanup, and honest limitations.

Initial service-level objectives after ingestion:

- useful viewer render p75 under 1.5 s on the sample project;
- uncached waveform query p95 under 500 ms and cached p95 under 100 ms for
  64 channels and a typical desktop viewport;
- at most two envelope values per horizontal pixel per channel;
- client payload under 5 MiB for normal viewports and no full-recording response;
- fast-test PR CI under 10 minutes;
- ingestion memory bounded by configured chunk size plus reader overhead;
- zero cross-workspace resource disclosures in authorization tests.

These are targets until measured; reports must label measurements distinctly.

## Analytics and discovery

Events use a vendor-neutral interface and contain resource category and coarse
format/status only—never samples, names, subject identifiers, annotations, or file
metadata. Events include the mission list from signup through invite/export.

Roadmap inputs are labeled `validated need`, `hypothesis`, `engineering`, or
`speculative`. Alpha interviews ask where users lose time, what they cannot share,
which formats fail, how they determine bad data, and what output their downstream
tool needs. No post-V1 feature is assumed validated.

## Release exclusions

No clinical claims, PHI guarantee, automated diagnosis, external AI processing,
model training on uploads, public-by-default sharing, arbitrary code execution,
Kubernetes, or enterprise-control theater.

