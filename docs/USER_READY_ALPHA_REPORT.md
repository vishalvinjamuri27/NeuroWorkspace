# User-ready public alpha report

Date: 2026-08-18  
Starting revision: `796e85b0ac949d149327ecef946092fd681499af`  
Clean-clone tested revision: `2c35573c1f959b97c5c9d0b79a5037e6e143b54f`  
Release channel: `0.1.0-alpha.1` (`0.1.0a1` in Python metadata)

## Executive summary

NeuroWorkspace moved from a source-complete alpha candidate to a repository-level
user-ready public-alpha package. A new visitor can understand the EEG use case,
open a legally sourced and visibly attributed public recording without an account,
exercise the viewer in about a minute, enter through local development auth or
hosted OIDC, import supported own data, annotate/QC/export, leave, return through a
workspace home, and submit privacy-bounded feedback. The application records the
meaningful activation, usage, failure, format, and return evidence needed to decide
what to build next.

All locally executable release gates passed, including a fresh checkout. Docker was
not installed, so image execution, Compose startup, and service-restart persistence
remain explicitly **BLOCKED / NOT RUN** and are mandatory deployment gates before
public invitations. A private GitHub source repository was created for backup and
collaboration; no hosted deployment, remote tag, GitHub Release, package/container
publication, outreach, public announcement, or user/adoption claim was created.

## What changed since `796e85b`

Five local commits organize the implementation and acceptance phase before the
publication-preparation commit:

- `6e82044`: user-ready alpha validation plan;
- `a899cf7`: live first-user audit and prioritized findings;
- `c436373`: sample/auth/workspace, product evidence, feedback, errors, and E2E
  implementation;
- `2c35573`: product-first distribution docs/media, release metadata,
  performance/security evidence, and deploy bootstrap separation;
- `53b85ad`: final user-ready alpha acceptance record.

No major feature category, new biosignal modality/format, ML/AI, live streaming,
diagnostic behavior, Kubernetes, mobile app, or enterprise collaboration was added.

## User-facing improvements

- Product-first landing copy names researchers, BCI developers, neurotechnology
  teams, validated formats, non-clinical scope, sample, import, workspace, and
  configurable source repository link.
- `/workspace` provides loading, auth, empty, error, project grouping, existing
  recording status, reopen, and import-into-project paths.
- Hosted mode now implements provider-neutral OIDC authorization code + PKCE with
  signed short-lived state, nonce, strict return path, discovery/issuer checks, and
  secure application session. Production cannot expose development auth.
- Upload reuses an existing session/project, explains durable processing and the
  workspace/project/recording relationship, and shows safe reference/recovery on
  failure.
- Viewer startup retries one transient fetch, separates demo and owned breadcrumbs,
  shows a one-minute guide, and records a later-session reopen.
- The persistent **Send feedback** action is available on landing, workspace,
  import, and viewer surfaces.

## Sample/demo experience

`scripts/seed_public_sample.py` fetches one pinned 3,215,672-byte EEGLAB file from
OpenNeuro ds004362 v1.0.0, verifies its manifest SHA-256, ingests it through the
real adapter/job path, and publishes one shared read-only recording. The dataset
declares CC0; DOI 10.18112/openneuro.ds004362.v1.0.0, source URL, license,
attribution, human-data status, and non-diagnostic limitations are visible without
displaying subject fields.

The release bootstrap is idempotent when both DB and canonical data are present,
and production readiness fails if the demo row/version is absent. A no-human-data
synthetic seed remains available for offline development/E2E but is not the hosted
sample claim.

Live browser re-audit completed landing → sample → 64-channel waveform → zoom →
pan → hide channel → QC. The checked-in screenshot is a capture of that real state.
`capture-product-media.mjs` provides a reproducible screenshot/WebM sequence and
uses generated own data for annotation because the shared sample is intentionally
read-only.

## Deployment readiness

The selected hosted shape is Next.js web, FastAPI API, independently runnable
Celery worker, managed PostgreSQL, managed Redis, private S3-compatible
source/export storage, OIDC, secret manager, and one shared durable POSIX network
volume for canonical Zarr. Kubernetes is not required.

Migrations and sample ingestion were removed from every API replica's startup. A
one-off Compose `bootstrap` service now runs them before the API, matching the
hosted release-job contract. Readiness verifies DB and, in production, the complete
sample. `PUBLIC_ALPHA_DEPLOYMENT.md` records configuration, order, health,
rollback, backups, cost drivers, scaling knobs, and exact blocked commands.

The main scalability constraint is explicit: every API and worker replica needs
the same canonical volume. The current implementation cannot safely use separate
ephemeral disks or claim that S3 alone holds active Zarr data.

## Docker/Compose validation status

`command -v docker` returned no path on 2026-08-18. Therefore:

- production API/web/worker image builds: **BLOCKED / NOT RUN locally**;
- Compose configuration through `docker compose config`: **BLOCKED / NOT RUN**;
- six dependencies/services plus bootstrap clean boot: **BLOCKED / NOT RUN**;
- stop/restart and user/project/recording/object/annotation persistence:
  **BLOCKED / NOT RUN**;
- live MinIO signed upload, Redis queue, PostgreSQL, health dependency behavior:
  **BLOCKED / NOT RUN**.

Static checks parsed the Compose YAML and found web, API, worker, bootstrap,
PostgreSQL, Redis, MinIO, and all four named volumes. Dockerfiles retain pinned
base versions and non-root runtime users. Static inspection is not recorded as a
container pass.

## Analytics events implemented

The allowlisted taxonomy supports:

`sample_demo_started`, `sample_recording_opened`, `signup_completed`,
`workspace_created`, `project_created`, `upload_started`,
`upload_completed`, `upload_failed`, `ingestion_started`,
`ingestion_succeeded`, `ingestion_failed`, `recording_opened`,
`viewer_zoomed`, `viewer_panned`, `channel_visibility_changed`,
`annotation_created`, `annotation_updated`, `annotation_deleted`,
`bad_channel_marked`, `qc_opened`, `processing_run_created`,
`export_started`, `export_completed`, `export_failed`, and
`recording_reopened`.

Only opaque IDs, random session ID, feature event, coarse format, broad size
bucket, allowlisted operation/export type, outcome class, and timestamp may enter
events. Client extra properties are rejected. Server-side events own mutation and
failure outcomes. No mouse-move stream exists.

## Feedback system

Feedback categories are bug, confusing workflow, missing feature, file import,
performance, other. A record may contain user-entered feedback, optional contact
permission/address, route, opaque workspace/recording IDs, and build version. The
UI explicitly warns against PHI, files, signal, subject fields, or clinical notes.
Nothing scientific is attached automatically. Admin feedback and metrics endpoints
return 404 outside `ALPHA_ADMIN_EMAILS`.

## E2E coverage

Five Chromium tests passed in the main checkout (10.8 seconds) and fresh clone
(11.8 seconds):

1. landing purpose, sample/import choices, warning, and clean console;
2. landing → sample → viewer → zoom/pan → channel toggle → QC and read-only state;
3. local signup → workspace/project → CSV upload/ingestion → waveform → annotation
   create/edit/delete → bad channel → QC → export → workspace reopen;
4. invalid CSV configuration → safe failure heading/reference/recovery;
5. User B receives a uniform 404 for User A's workspace.

The own-data E2E also stores an HTML/onerror annotation sentinel, verifies it renders
as text, and confirms it did not execute. Tests use state assertions; no fixed
sleep was added to the E2E specifications.

## Real-data validation

`make real-data` re-verified all checksums and passed 6/6 acceptance tests in
38.41 seconds: PhysioNet Sleep-EDF EDF/EDF+ and annotations, public BioSemi BDF,
OpenNeuro EEGLAB, OpenNeuro BrainVision plus BIDS foundation, and official MNE FIF.
Generated CSV is covered by fast/API/E2E tests. The MNE Sleep-EDF reader emitted
four existing annotation-range warnings; assertions passed.

The public sample was separately downloaded from an empty clean-clone cache,
checksum-verified, and ingested successfully. No recording is committed to Git.

## Performance observations

All specified generated cases completed on a 24 GiB Mac/local APFS:

| Case | Ingest | Peak RSS increase | Local viewport p50/p95 |
|---|---:|---:|---:|
| 32 ch, 30 min, 256 Hz | 2.097 s | 195,706,880 B | 36.813 / 42.114 ms |
| 64 ch, 2 hr, 512 Hz | 25.248 s | 300,400,640 B | 49.973 / 53.297 ms |
| 128 ch, 8 hr, 512 Hz | 210.100 s | 324,272,128 B | 48.146 / 59.876 ms |

The largest logical float32 raw matrix was 7,549,747,200 bytes and its canonical
base+pyramids used 11,363,191,868 bytes. Pan/zoom store queries remained roughly
37–55 ms p50 across cases. The measured automation surface did not expose reliable
per-tab heap/dropped-frame counters, so browser memory and quantitative smoothness
remain unclaimed. See `PERFORMANCE_VALIDATION.md`.

## Security review results

Executable checks passed for cross-user workspace/recording/export isolation,
deleted-recording denial and canonical cleanup, malicious annotation text,
filename/path traversal, oversized/unsupported upload, missing BrainVision
companions, malformed import UX, session/OIDC state tamper, Origin enforcement,
and analytics sentinel exclusion. No cross-tenant disclosure was found.

Live S3 presign/CORS/IAM/expiry/deletion and parser container isolation were not
exercised without the target provider/Docker runtime. Rate limits, ingress web
CSP/HSTS, cloud network policy, backup restore, retention/privacy notices, and a
human incident owner are deployment gates.

## Final validation ledger

- `make lint`: PASS.
- `make typecheck`: PASS, mypy strict over 35 source files plus TypeScript.
- `make test`: PASS, 19 fast Python + 2 frontend tests.
- `pnpm -r format:check`: PASS after narrowing checks to maintained web files.
- `make build`: PASS, Python sdist/wheel `0.1.0a1` and optimized Next build.
- `CI=1 pnpm test:e2e`: PASS, 5 Chromium tests.
- `make real-data`: PASS, 6 real-data tests.
- fresh migration upgrade/check/downgrade/upgrade: PASS.
- Python `pip-audit --strict`: no known vulnerabilities.
- `pnpm audit --audit-level high`: no known vulnerabilities.
- public sample checksum/fresh ingestion: PASS.
- Docker/Compose/restart: BLOCKED / NOT RUN.

## Clean-clone acceptance

A `git clone --no-local` of `2c35573` into
`/tmp/neuroworkspace-clean-clone.fY4mcO/repo` completed:

1. frozen Python/frontend dependency install;
2. fresh SQLite migration through `6c9b78f0c1ad`;
3. empty-cache OpenNeuro download, checksum, and public-sample ingestion;
4. migration drift check;
5. lint and strict types;
6. 19 fast Python and 2 frontend tests;
7. Python and production web builds;
8. local API/web boot through the E2E harness;
9. sample view, own-data upload/ingestion, waveform, annotation, QC, export, reopen,
   safe failure, and authorization through the automated flows.

Persistence inside the local deployment is evidenced by workspace reopen after
ingestion. Full service-stop/restart persistence is part of the blocked container
procedure and is not silently inferred from this result.

## GitHub Publication

- Repository name: `NeuroWorkspace` (`vishalvinjamuri27/NeuroWorkspace`).
- Repository owner: `vishalvinjamuri27`.
- Privacy status: **PRIVATE**, verified through GitHub metadata before and after
  the push.
- Remote configured: `origin` for fetch and push at the private GitHub repository.
- Branch pushed: `main`; GitHub reports `main` as the default branch.
- Final local commit: `refs/heads/main` at publication verification.
- Final remote commit: `refs/remotes/origin/main` at publication verification.
- Commit parity: **PASS**; both refs resolved to the same full commit hash after
  the final push. The exact resolved hash is included in the completion record;
  this report uses immutable ref names because it is itself part of that commit.
- Secret scan: **PASS**. Gitleaks 8.30.1 scanned all 16 repository commits
  with no leaks; a second detector found no verified secrets; manual current-tree
  and history checks found no credential patterns, private/raw EEG files, local
  database files, private-key material, or machine-specific user paths in Git.
- Working-tree status: clean after the final commit and push.
- Publication status: **PASS — private source control, backup, and
  collaboration-ready GitHub state only**.
- Release status: no GitHub Release, package, container image, public repository,
  public announcement, or community submission was created.

## Known blockers

Before sending a hosted URL to strangers, the operator must:

1. pass Docker image/Compose boot and restart-persistence checks;
2. validate live S3 presign/CORS/private access/deletion and shared-volume mounts;
3. configure and smoke a real OIDC client;
4. configure public web/API/repository URLs, TLS, ingress headers/rate limits;
5. run backup/restore and identify monitoring/on-call/incident ownership;
6. publish appropriate terms/privacy/non-clinical/data-eligibility notice.

These are deployment/operational blockers, not requests for new product features.

## Known usability risks

- The sample teaches annotations through events/location but cannot be edited; a
  participant must import permitted data for mutation.
- Workspace/project terminology may still require observation with users who do
  not organize recordings that way.
- Long remote uploads and cloud-volume contention have not been observed on real
  participant networks.
- Supported-format producer variants may fail despite the pinned corpus.
- Feedback triage is an operator endpoint, not a polished admin dashboard.
- Return behavior is later browser session, not calendar-day retention.
- Accessibility and browser-memory behavior need participant-device evidence.

## Product metrics now measurable

- sample starts and successful sample opens;
- new identities, workspaces, and projects;
- upload starts/completions/failures and ingestion starts/successes/failures;
- safe format and size-bucket distribution;
- owned recordings opened, later-session reopened, and users with 2+ successful
  ingestions;
- users who zoom, pan, change channel visibility, open QC, annotate, mark a bad
  channel, process, and export;
- activation, upload activation, annotation usage, return behavior, and
  multi-recording rates.

No external milestone M1–M8 is claimed.

## What external user sessions should test

Observe whether five EEG users, without a tour, can explain the product, open the
sample, navigate a time range, hide channels, import a permitted own recording,
annotate, mark a channel, interpret QC as a review suggestion, export, leave, and
reopen. Record wrong turns, help needed, current-tool comparison, reason to return,
and reason not to return. Compare observed actions with aggregate events, never
with sensitive payloads.

The most important unknown is not whether the software can add features. It is
whether this workflow is valuable enough to repeat.

## What NOT to build yet

Do not build live streaming, new modalities/formats, ML/AI, diagnostic behavior,
Kubernetes, mobile apps, enterprise SSO/SCIM, or elaborate collaboration without
validated repeated demand. Follow `ROADMAP_FREEZE.md`; only validated and repeated
high-value requests normally enter the near-term roadmap.

## Exact next action for the human founder

1. Choose one container platform/region, PostgreSQL, Redis, private S3, shared
   network volume, domain, OIDC provider, and a named on-call owner.
2. Follow `PUBLIC_ALPHA_DEPLOYMENT.md` and do not invite anyone until every
   blocked container/restart and live-storage/auth check passes. Record the result;
   do not ask for more features.
3. Put the final HTTPS sample URL into the deployment and keep
   `NEXT_PUBLIC_REPOSITORY_URL` empty while the repository is private. Share the
   private repository only with approved collaborators. Add a public repository
   URL only after a separate explicit publication decision, then verify the
   screenshot/README links from a signed-out browser.
4. Personally recruit **five named, opt-in EEG practitioners** across at least
   three target segments using warm introductions or community channels that allow
   tool feedback. Book five separate 20–30 minute sessions within two weeks.
5. Send only the sample URL, scope/data warning, and session time. Do not send a
   feature tour.
6. Run `USER_TESTING_PLAYBOOK.md`, store one private
   `templates/USER_SESSION.md` note per session, and review safe metrics/feedback
   after each.
7. After session five, cluster observed blockers and decide: fix one repeated severe
   problem, narrow the audience, continue unchanged, or stop. Do not resume
   speculative engineering by default.

With the documented pre-invite deployment gates enforced, NeuroWorkspace is ready
for external alpha user testing.
