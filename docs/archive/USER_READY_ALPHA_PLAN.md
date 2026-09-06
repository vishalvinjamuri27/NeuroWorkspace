# User-ready public alpha plan

Date: 2026-08-18  
Starting commit: `796e85b0ac949d149327ecef946092fd681499af`

## Objective

Move NeuroWorkspace from a source-complete alpha candidate to a public-alpha
package that can be placed in front of external EEG users and produce trustworthy,
privacy-safe evidence about activation, core use, return behavior, and failure.
This phase improves distribution and validation; it does not expand the scientific
product category.

## Starting evidence

The repository was clean and exactly at the requested starting commit. The unchanged
baseline passed Python and frontend lint, strict Python/TypeScript checks, 16 fast
Python tests, 2 frontend unit tests, 2 Chromium E2E tests, Python package build, and
the optimized Next.js build. Public-real-data and coverage results remain those in
`ALPHA_READINESS_REPORT.md` until rerun at final acceptance.

Docker and the Docker Compose CLI are absent on the validation workstation. Image,
Compose, and restart-persistence execution therefore remain blocked unless the
runtime becomes available during this phase.

## Audit hypotheses to verify in the UI

- **BLOCKER:** the sample route depends on an operator running the seed command;
  failure asks a public visitor to run a development command.
- **HIGH:** the sample is synthetic and read-only, but attribution/provenance and
  the relationship between sample behavior and supported real files are not
  explained in the viewer.
- **HIGH:** the import flow creates a new workspace/project on every attempt and has
  no return/reopen surface, making later-session continuation difficult to discover.
- **HIGH:** product analytics are partial audit rows, not an allowlisted event
  taxonomy or retention/activation query.
- **HIGH:** users have no in-product feedback path and failures do not consistently
  expose a copyable safe reference.
- **MEDIUM:** viewer controls are compact and capable, but sample visitors receive
  no short task-oriented orientation.
- **MEDIUM:** the README begins with accurate positioning but lacks a deployable demo
  URL contract, workflow media, and a product-first one-minute evaluation path.

The browser-based first-user audit will confirm, reject, and reprioritize these
hypotheses before implementation.

## Work packages

### 1. First-use and sample path

- Audit the complete landing, sample, upload, viewer, QC, annotation, export, and
  return path in a running browser.
- Make sample seeding idempotent and part of a documented deployment/startup flow.
- Show source status, attribution, read-only intent, and concise next actions inside
  the sample viewer.
- Add a small authenticated workspace/recording home so a returning user can reopen
  prior work instead of creating another workspace by accident.
- Preserve current scientific behavior and format boundaries.

### 2. Safe product evidence

- Add an allowlisted `ProductEvent` persistence model and service. Reject unknown
  events/properties rather than accepting arbitrary analytics payloads.
- Record the required funnel, viewer, annotation, QC, processing, export, and safe
  format/size-bucket events at server-authoritative boundaries where possible.
- Use a browser endpoint only for interaction events such as zoom, pan, channel
  visibility, QC tab opening, and sample-start intent.
- Add privacy-safe session identifiers and server-derived opaque actor/resource IDs;
  never accept filenames, labels, free text, source metadata, or signal values as
  event properties.
- Add an owner/admin metrics endpoint or script that calculates activation, upload
  activation, annotation use, return behavior, and multi-recording behavior.

### 3. Feedback and supportability

- Add a compact `Send feedback` entry in application surfaces with a fixed category,
  free text, optional contact permission, current route, opaque context IDs, and
  build version.
- Keep feedback in the deployment database for the alpha and document operator
  retrieval/export. Do not attach data or scientific metadata.
- Generate diagnostic IDs for worker failures, retain safe internal diagnostics,
  and return a copyable error reference without exposing stack traces or paths.
- Add focused malformed/unsupported/missing-companion failure tests.

### 4. Distribution package

- Rewrite the README top section around the EEG user workflow, sample path,
  screenshot/media, validated formats, and a configurable hosted-demo URL.
- Capture a real running workspace screenshot and provide a reproducible Playwright
  demo-media procedure; create a GIF only if reliable capture tooling is available.
- Publish concrete hosted deployment, analytics, user testing, outreach, security,
  roadmap-freeze, and release-note documents.
- Recheck license, conduct, contribution, security, templates, real-data instructions,
  and developer commands from an external-contributor perspective.

### 5. Acceptance and evidence

- Expand E2E coverage for sample, new-user/reopen, failure, and authorization flows
  without fixed sleeps.
- Rerun threat-model cases at the API boundary, including exports and deletion.
- Measure small user-facing ingest/query behavior and attempt medium/large cases only
  within workstation time/memory limits; label unmeasured cases plainly.
- Run lint, types, tests, migrations, real-data acceptance, builds, and dependency
  audits from a fresh clone.
- Attempt Docker again. If still absent, preserve `BLOCKED` and publish exact Linux
  validation and restart-persistence commands.

## Release blockers

- Any cross-workspace recording, export, annotation, QC, or object access.
- Sample path unavailable after the documented deployment start sequence.
- Upload/ingestion failure without a safe actionable message and reference.
- Analytics accepting sensitive or arbitrary properties.
- A required fast, E2E, real-data, type, migration, build, or audit gate failing.

Docker runtime validation is an environmental blocker, not grounds for a false pass.
A public hosted deployment itself requires the human operator to choose accounts,
region, domain, OIDC provider, and spending authority.

## Definition of completion

Completion means a stranger can understand the product, open the sample, perform
the core viewer review, upload and later reopen their own supported data, report a
problem safely, and generate measurable privacy-conscious product evidence. The
repository must provide a concrete deployment path and user-session materials, be
clean and reproducible, and explicitly freeze speculative roadmap expansion.

The phase ends with `USER_READY_ALPHA_REPORT.md` and a concrete founder action for
recruiting five EEG users. No remote tag, deployment, outreach, or user claim is
created without explicit authority and external credentials.
