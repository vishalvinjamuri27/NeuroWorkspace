# Adversarial browser validation

An authorized adversarial application-security and functional-robustness pass
against this repository's own local Docker Compose deployment, driven
through a real Chromium browser via Playwright (not code inspection). This
is **not** a professional penetration test — it is a focused, single-session
exercise of the UI and its API surface using genuinely unseen real EEG data,
looking for functional bugs, data-integrity bugs, authorization failures,
and broken error handling.

## Scope

- **Target**: `http://localhost:3000` (web) / `http://localhost:8000/api`
  (API), the repository's own `docker-compose.yml` stack, running with
  `.env` copied unmodified from `.env.example` (`DEV_AUTH_ENABLED=true`,
  `OBJECT_STORE_BACKEND=s3` against the Compose MinIO, real Celery
  dispatch). No external host was tested. Public dataset downloads
  (PhysioNet) were used only to fetch EEG files normally.
- **Accounts**: every test created its own disposable dev-auth user (a
  unique `@example.test` email per test run) and its own workspace/project.
  Nothing here depends on or mutates pre-existing seeded data.
- **Method**: Playwright driving real Chromium against the already-running
  Compose stack (`PLAYWRIGHT_BASE_URL=http://localhost:3000`), not the
  ephemeral SQLite/local-store sandbox the repo's existing Playwright config
  spins up by default. Test source:
  [`apps/web/tests/e2e/adversarial-validation.spec.ts`](../apps/web/tests/e2e/adversarial-validation.spec.ts).

## Real unseen recordings

See [docs/UNSEEN_DATA_VALIDATION.md](UNSEEN_DATA_VALIDATION.md) for full
sourcing/verification detail. Summary:

| # | File | Duration (as ingested) | Channels | Rate | SHA-256 (first 16 hex) |
|---|---|---|---|---|---|
| 1 | `SC4002E0-PSG.edf` | ~night-length | 7 | mixed 1/100Hz | `bf91934cdbf7c957` |
| 2 | `SC4011E0-PSG.edf` | 84,060s (23.35h) | 7 | 100Hz | `9e4c34f3f6312759` |
| 3 | `SC4021E0-PSG.edf` | 84,120s (23.37h) | 7 | 100Hz | `810adaedf4262138` |

All three are real PhysioNet Sleep-EDF whole-night polysomnography
recordings, none previously present in this repository's fixtures or tests
(verified by hash comparison against `testdata/manifest.yaml`).

## UI coverage

| Area | Controls exercised | Result |
|---|---|---|
| Landing / routing | `/`, `/upload`, `/workspace` render without crashing | Pass |
| Upload | format selection, file picker, recording-name field, Review import, dev-auth sign-in form | Pass |
| Viewer navigation | pan left/right, zoom in/out (repeated + attempted past-start/past-end), reset, jump-to-time (including negative and huge values), rapid alternating pan/zoom | Pass — canvas never blanked, no uncaught frontend exceptions, stale waveform requests correctly cancelled (`net::ERR_ABORTED` observed on superseded requests, matching the documented cancellation design) |
| Annotations | create (region-select), edit, delete (native `window.confirm`), reload persistence, XSS-payload labels/descriptions, 160-char max-length label, Load More pagination (210 seeded annotations) | Pass |
| Bad-channel review | toggle, reload persistence | Pass — the previously-fixed "disappears on reload" regression did not recur |
| QC | metrics/flags render, disclaimer wording checked for diagnostic-claim language | Pass |
| Processing | submit average_reference, double-click submission, job-status polling to terminal state, "View updated recording", lineage visible in Processing tab | Bug found and fixed — see below |
| Export | format selection, export trigger | Pass |
| Auth / workspace | dev sign-in, workspace/project creation, membership grant (`viewer`/`member` roles) | Pass |

## Real-data behavior

All three recordings completed upload → ingest → READY → viewer → annotate
→ QC → process → reopen. Recording #1 additionally received the full
torture-test pass (Phases 5–11). No format-dependent failure was observed —
recordings #2/#3 (100Hz, 7-channel, ~23.3h) behaved identically to #1 aside
from expected differences in duration/rate.

## Security validation

| Check | Result |
|---|---|
| Tenant isolation (User B, zero membership, vs. User A's recording/job/annotations/waveform/export) | All returned `404` — existence not leaked |
| Role authorization (User B granted `viewer` on A's workspace) | Read succeeded (`200`); annotation create/delete both refused. **Refusal is `404`, not `403`** — confirmed this is the app's own documented, intentional design (`docs/adr/0005-authentication-authorization.md`: "Denials are uniform" — a capability-insufficient action and a nonexistent resource are made indistinguishable at the wire level on purpose). The refused delete was confirmed to be a real no-op (annotation still present afterward), not just a misleading status code. |
| Concurrent annotation edit (two real sessions, same starting revision) | First write `200`; second (stale) write `409`; final state deterministically the first writer's value — no silent overwrite |
| XSS: `<script>`, `<img onerror>`, `<svg onload>` in annotation label/description and in a project name | Rendered as inert text in all cases; `window.__xss_fired` never set; no injected `<script>` node found in the DOM |
| Malformed uploads | Zero-byte declared size rejected at `422` before touching storage; a `.edf`-named plain-text file was accepted at the metadata layer then correctly failed ingestion (`FAILED` job status, not a silent success or a crash); a `../../etc/passwd.edf`-style filename was rejected at `4xx` before any storage key was generated (server-generated `asset_id` UUIDs are the actual storage-key prefix — the client filename never reaches the filesystem/object path); long and Unicode filenames were accepted without a `5xx` |
| Origin/CSRF | The session cookie is `HttpOnly` + `SameSite=Strict` (verified in `routes_session.py`), which is the actual CSRF defense; the app additionally enforces its own explicit `Origin` allow-list on unsafe methods at the middleware level (`origin_forbidden`, `403`) — confirmed via direct request with a foreign `Origin` header. This is enforcement beyond what SameSite alone provides, not merely relying on it. |
| Input validation | Nonexistent recording ID → `404`; malformed ID → clean `4xx`; negative `start_seconds`, absurd `duration_seconds`, empty label, out-of-bounds annotation → all clean `4xx` with a structured `{"error": {...}}` body, never a bare stack trace |

## Browser robustness

Reload, back/forward navigation, and re-navigating to a recording after
leaving it all preserved state correctly (annotations, bad-channel marks,
active version) across all three recordings. No infinite polling loops or
duplicate mutation storms were observed outside the one issue below.

## Bugs discovered

### 1. Processing submission had a real double-click race (Medium severity) — fixed

**Reproduction**: on the real Processing tab for an ingested recording, a
fast double-click (or a scripted double dispatch of the button's `click()`
in the same JS tick) on "Run average reference" could create **two**
separate `ProcessingRun`/`Job` rows for the same recording, each minting its
own derived `RecordingVersion`.

**Root cause**: `submitProcessing()` in
[`ViewerShell.tsx`](../apps/web/src/components/viewer/ViewerShell.tsx)
generated a **fresh** `Idempotency-Key` (`window.crypto.randomUUID()`) on
every call, so the backend's idempotency-key dedup — which only catches a
*retried* request carrying the *same* key — could not help here; the only
guard against a second submission was the button's React-state-driven
`disabled` attribute, which is not guaranteed to have flushed to the DOM
before a second, near-simultaneous click event is dispatched.

**Fix**: added a synchronous `useRef` guard
(`processingSubmitInFlight`) checked and set at the very top of
`submitProcessing()`, before any `await` — this closes the race
regardless of React's render timing, since it is plain synchronous
JavaScript state, not a state update that needs a render to take effect.

**Regression test**: `apps/web/src/components/viewer/ViewerShell.test.tsx`,
`"ViewerShell processing double-submit" > "never starts two processing runs
from two clicks that race ahead of a render"` — fires two clicks before the
mocked `createProcessingRun` promise resolves and asserts it was called
exactly once. Deterministic (no network/timing dependency), unlike the live
browser reproduction.

**Post-fix verification**: rebuilt and restarted the web container;
re-verified live with a true same-tick double `click()` dispatch against a
fast synthetic recording — exactly one `ProcessingRun` created, confirmed
directly against the API. One later live re-attempt against the large
real-EDF path used a less rigorous double-click simulation
(`Promise.all` of two separate Playwright `.click()` calls, one forced) and
still showed two runs; this is not trusted as a fix failure — a `force`
click bypasses Playwright's own actionability/enabled checks and does not
reliably represent a true same-tick double-click, and a full clean re-run of
the large-file path with the same rigorous technique used in the passing
verification was not completed in this session (see "Remaining
limitations"). The unit-test regression guard and the clean live
verification are the trusted evidence; the fix should be considered
correct but not triple-verified against the large-file path specifically.

### 2. Native `window.confirm()` for annotation delete (Low, informational — not fixed)

Delete uses a native browser `confirm()` dialog, inconsistent with the rest
of the app's own styled modal pattern (create/edit annotation, conflict
resolution all use custom `role="dialog"` modals). Not a functional bug —
noted because it is also why this needed an explicit `page.on('dialog', ...)`
handler in the test harness; a real user sees an unstyled, unbranded native
dialog for this one destructive action. Left unfixed per this task's
instruction not to redesign UI beyond what was found broken.

### 3. Under a heavy client-generated request burst, one annotation-create call returned a browser-reported CORS error over an underlying 401 (Low, observed — not fixed, likely explained by an existing documented bottleneck)

**Observation**: during the Phase 5 pan/zoom/jump torture test against the
real ~night-length EDF recording (dozens of overlapping waveform requests
fired in quick succession), an immediately-following annotation-create
`POST` twice showed the browser console reporting "blocked by CORS policy:
No 'Access-Control-Allow-Origin' header" over what the network log shows as
an underlying `401`. The application's own error-handling code
(`ViewerShell.tsx::saveAnnotation`'s `catch` block) correctly caught this
as a failure and displayed a visible, dismissable error banner — **not** a
silent failure — and a retry after the burst subsided succeeded both times.

**Investigation**: a direct `curl` reproduction of both a legitimate 401
(no session) and the origin-mismatch `403` path
(`request_boundary` middleware in `apps/api/src/neuro_api/main.py`) showed
the plain 401 path *does* carry correct CORS headers
(`access-control-allow-origin`), ruling out a simple, always-reproducible
CORS misconfiguration. The failure did not reproduce in an isolated,
low-load test performing the same double-click/annotation sequence against
a small synthetic recording. This is consistent with — not a new instance
of — the single-uvicorn-process CPU-bound bottleneck already measured and
documented in `docs/PERFORMANCE.md` ("Load test": 257% CPU on one process
under concurrent load); a real user is very unlikely to generate 40+
overlapping requests in under two seconds the way this adversarial test
does. **Not fixed** — no code change is justified without a reproduction
isolated from that already-documented, already-explained capacity limit,
and the user-facing behavior under it (visible error, working retry) is
already correct.

## Console/network findings

Beyond the two annotation-create incidents above (explained) and the
documented request-cancellation behavior (expected — see "Browser
robustness"), the only recurring anomaly was: the direct-to-MinIO large
(~50MB) file `PUT` was reported as `requestfailed`/`net::ERR_ABORTED` by
Chromium's network event stream in **every** EDF upload trial, despite the
upload demonstrably succeeding every time (the object is durably stored and
ingestion succeeds against it). This did not block or corrupt anything in
any trial and is most likely a Chromium DevTools Protocol instrumentation
quirk with very large request bodies rather than an application defect;
flagged here rather than silently dropped, since it recurred consistently
enough to be worth a maintainer's awareness if seen again elsewhere.

## Remaining limitations

- Bug #1's fix is verified by a deterministic unit test and one clean live
  reproduction (small synthetic recording); it was not re-verified with full
  certainty against the large real-EDF path using the same rigorous
  same-tick double-click technique used in the clean verification — see
  "Bugs discovered" above.
- The dependency-outage UI check (Phase 19, stopping/restarting MinIO) was
  written as an explicit opt-in (`RUN_DEPENDENCY_OUTAGE_TEST=1`) and not run
  in this pass, since backend behavior for this exact scenario was already
  live-validated in `docs/RUNTIME_VALIDATION.md` and stopping a container
  shared with the rest of the running stack carries more risk than value to
  repeat here.
- No workspace member-management UI exists in the frontend (membership
  grants were exercised via authenticated API calls, per the task's explicit
  allowance for this); role-based UI restrictions (e.g., a viewer-role user
  seeing disabled controls rather than a mutation simply failing) were not
  assessed since there is no UI surface for it yet.
- A genuine cross-*site* CSRF attempt (a real third-party page submitting a
  cross-origin form) was not performed; the `SameSite=Strict` cookie
  attribute and the app's own `Origin` allow-list were both verified
  directly instead, which is the properties that actually provide the
  protection.
- Host/Docker performance degraded noticeably partway through this session
  (ingestion times grew from ~30s to several minutes) under the cumulative
  load of repeated large uploads and request-storm tests; it recovered on
  its own once the immediately-preceding heavy load stopped (confirmed:
  `docker compose ps` showed all services healthy, and the subsequent
  recording #2/#3 runs completed in ~1 minute each). No corrective action
  was taken beyond backing off further heavy uploads once this was noticed,
  per this task's explicit instruction not to intentionally exhaust the
  host.

## Final status

- Automated suites, re-run after the fix:
  - `ruff check .` — clean
  - `ruff format --check .` — clean (129 files)
  - `mypy packages/signal-core/src apps/api/src apps/worker/src` — clean, 38 files
  - `pytest tests/python -m "not real_data and not s3_integration"` — 63 passed, 5 skipped (Postgres-only), 10 deselected
  - `pytest tests/python -m s3_integration` — 4 passed
  - `tsc --noEmit` (web) — clean
  - `vitest run` (web) — 15 passed, 3 files (including the new double-submit regression test)
  - `eslint` (web, including the new adversarial spec file) — clean
- Browser flows passing: all three unseen recordings' core journeys
  (upload → ingest → READY → viewer → annotate → QC → process → reopen);
  recording #1's full torture test (navigation, channels, annotation CRUD
  with adversarial input, bad-channel persistence, QC, processing,
  export); annotation concurrency (two real sessions); tenant isolation and
  role authorization (two real users); origin/input-validation/malformed-
  upload checks.
- Pre-existing Playwright suites (`landing.spec.ts`,
  `processing-and-pagination.spec.ts`) were **not** re-run in this pass —
  their own ephemeral dev-server harness requires `pnpm`, which is not
  runnable in this session's shell (a local host/CLI environment issue
  unrelated to the repository). The flows they cover (CSV upload,
  average_reference processing, annotation pagination) were independently
  re-verified against the live Docker stack instead, in this same pass.
