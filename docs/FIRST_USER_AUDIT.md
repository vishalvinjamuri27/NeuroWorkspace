# First-user audit

Date: 2026-08-18  
Starting revision: `796e85b0ac949d149327ecef946092fd681499af`  
Audit environment: fresh SQLite database, freshly seeded demo recording, local API and Next.js development servers, Chromium through the Codex in-app browser.

## Method

The audit followed the product as an external EEG researcher rather than as a repository maintainer:

```text
discover -> understand -> sample -> enter app -> create workspace/project
-> upload -> ingest -> inspect -> annotate -> leave -> return -> continue
```

The unchanged baseline passed lint, type checking, Python and frontend tests, the production build, and the existing Chromium E2E suite before the audit. Docker was not present, so the container path could not be exercised.

## What already works

- The landing page explains the product in plain language, names the validated formats, avoids medical claims, and makes **Try a sample EEG** the primary action.
- A seeded sample opens a real, interactive waveform viewer. Zoom, pan, channel visibility, events, metadata, and QC surfaces are present.
- The sample is read-only and the recording response discloses that it is deterministic synthetic data with no human data.
- The own-data import flow has a clear five-step progress model and says that filenames and subject metadata are excluded from analytics and logs.
- The viewer exposes provenance-oriented concepts without requiring users to understand the storage architecture.

## Prioritized findings

### BLOCKER

#### B1. The sample is not self-establishing

`Try a sample EEG` depends on an operator having run a separate seed command. When the demo row or its object data is absent, the current product can only tell the visitor that the sample is unavailable. A public alpha cannot send a visitor to an operator command.

**Fix in this phase:** add an idempotent sample bootstrap/verification command, make it an explicit deployment health gate, give the public UI a safe retry path, and document the source and attribution in-product. Do not silently generate mutable per-user copies.

#### B2. The own-data path is development-only in the browser

The first screen says **Development sign-in**, pre-fills `.test` credentials, and explains that production requires OIDC, but the browser has no production sign-in handoff. An external hosted user cannot enter the authenticated workflow using the shipped UI.

**Fix in this phase:** introduce an explicit frontend alpha access mode: local development keeps the signed development session; a hosted deployment must provide a configured login URL and the UI must refuse to masquerade as production when it is missing. Document the auth boundary and deployment gate. Full enterprise SSO is out of scope.

#### B3. The sample produced a transient first-open failure

In the observed run, the first navigation from the landing page rendered `Recording unavailable / Failed to fetch`; reloading immediately opened the viewer and all API requests returned `200`. Even if development hot reload contributed, the user receives no retry action or safe error reference.

**Fix in this phase:** make initial resource loading tolerant of a transient request failure, add a retry action, preserve a copyable safe reference, and cover landing-to-sample in E2E.

### HIGH

#### H1. There is no return/reopen home

`Open workspace` routes to the new-upload wizard. A returning user cannot see their existing workspaces, projects, or recordings and must know a recording URL.

**Fix in this phase:** add a compact workspace home with meaningful empty, loading, error, and ready states; list existing projects/recordings; link directly to reopen and import into a chosen project.

#### H2. Upload always implies creating new containers

The wizard asks for workspace and project names during account entry. It does not explain the hierarchy and makes repeat uploads create organizational clutter.

**Fix in this phase:** explain the relationship and allow the return surface to launch import into an existing project. Keep first-use creation convenient.

#### H3. Sample provenance is technically present but not discoverable enough

The header says `Public sample` while the recording body says `SYNTHETIC recording`. Attribution and the no-human-data limitation live only in metadata. “Public sample” could be read as a public human recording.

**Fix in this phase:** use consistent `Demo sample` wording, show source/attribution and limitations in a visible sample guide, and never use the public-sample breadcrumb for owned recordings.

#### H4. The one-minute sample journey has no orientation

The viewer contains the required controls, but a new visitor is not told to zoom, pan, hide a channel, open metadata/QC, or understand that annotations are demonstrated but disabled on the shared sample.

**Fix in this phase:** add a dismissible, short sample guide anchored in the viewer. It must point at real controls and avoid taking over the interface.

#### H5. Product evidence is incomplete

The database has audit records for security-sensitive mutations, but not the activation, usage, retention, format, or failure taxonomy required to decide what to build next.

**Fix in this phase:** add an allowlisted, privacy-safe product event store; server-derived format and broad size buckets; a small metrics query; and tests that prohibit sensitive event properties.

#### H6. There is no direct feedback route

No persistent in-app action lets a user report a confusing workflow, import failure, or performance issue with a safe context reference.

**Fix in this phase:** add in-app feedback with fixed categories, explicit “do not include PHI” copy, optional contact permission, opaque context IDs, app version, and an operator query path.

#### H7. Import failures are not supportable enough

Users can see a message, but there is no consistently exposed diagnostic/reference ID. Internal job diagnostics are not reliably populated with the adapter, format, version, failure class, and job/recording context.

**Fix in this phase:** populate a diagnostic ID, log allowlisted structured context, expose only the safe reference to users, and add a copy action.

### MEDIUM

- The README begins credibly but needs product media, an explicit hosted-demo placeholder, and a shorter route from interest to the sample.
- Landing-page audience copy does not explicitly name researchers, BCI developers, and neurotechnology teams.
- The viewer has keyboard interactions but no compact shortcuts hint beyond wheel/click language.
- Upload progress communicates stages but not a “safe to leave and reopen” expectation.
- Metrics are not documented as measurements versus targets, which could invite overclaiming.

### LOW

- `How it works` and `Capabilities` currently lead to the same section.
- The landing preview uses an illustrative filename-like label; it should be explicitly labeled as an illustration so it cannot be mistaken for shipped data.
- Some technical terms such as “extrema-preserving pyramid” are useful for experts but should stay below the first-use task language.

## Scope decision

This phase will address every BLOCKER and HIGH finding. Medium issues will be fixed only when they fall naturally inside the same files. Live streaming, new modalities, new format categories, ML, collaboration expansion, and enterprise identity are not justified by this audit.

## Re-audit acceptance checks

- A fresh public deployment cannot report healthy until the shared demo is usable.
- Landing -> sample -> waveform -> zoom/pan -> channel toggle -> QC/metadata works without reload or documentation.
- Hosted mode exposes a real configured login action or an explicit operator configuration error, never development credentials.
- A signed-in user can leave, return to `/workspace`, and reopen a recording.
- Unsupported/broken uploads present an actionable message plus a safe reference.
- Product events contain only documented allowlisted properties and support the defined alpha ratios.
- Feedback can be submitted without attaching files, signal data, metadata, filenames, or annotation contents.

## Re-audit result

All BLOCKER and HIGH findings were addressed and re-exercised:

- B1: production readiness now requires a usable demo; the release/bootstrap job
  checksum-verifies and ingests the pinned OpenNeuro sample.
- B2: hosted mode exposes provider-neutral OIDC authorization code + PKCE and
  refuses to show local credentials when it is not configured.
- B3: viewer startup retries one transient load, exposes retry/recovery, and carries
  safe API/job reference IDs.
- H1/H2: `/workspace` lists workspace/project/recording hierarchy, reopens work,
  and imports into an existing project.
- H3/H4: the shared sample is consistently read-only, visibly attributed, and
  includes a one-minute control/QC/metadata guide.
- H5: product events and aggregate ratios are allowlisted and privacy-tested.
- H6: persistent in-app feedback uses fixed categories and explicit data warnings.
- H7: ingestion failures provide a diagnostic reference backed by safe structured
  worker context.

The five-flow Chromium suite covered landing/sample/QC, own-data
signup→workspace→project→ingestion→annotation→export→reopen, visible failure
recovery, and two-user authorization. The remaining public-launch blocker is
environmental: Docker/Compose and restart persistence were not executable on this
host and must pass on the deployment environment.
