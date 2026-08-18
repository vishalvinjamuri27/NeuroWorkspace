# Public-alpha security review

## Result

No known cross-tenant data disclosure was found in the executable public-alpha
checks. Resource existence remains hidden with uniform 404 responses. Production
configuration fails closed without hosted OIDC, HTTPS issuer, private
S3-compatible storage, a long session secret, disabled development auth, and a
ready demo requirement.

This is an application-level recheck, not a penetration test, cloud IAM review,
container sandbox proof, or HIPAA assessment. Container execution remains blocked
on this host.

## Rechecked paths

| Scenario | Control / evidence | Status |
|---|---|---|
| User B guesses User A workspace/recording | membership-scoped lookups and two-account API + Chromium flows return 404 | PASS |
| User B downloads User A export | export lookup rechecks recording export capability; integration assertion returns 404 | PASS |
| Deleted recording access and objects | tombstone hides immediately; eager deletion test removes canonical version and rejects access | PASS |
| Malicious annotation rendering | React text rendering; E2E stores an HTML/onerror sentinel, displays it as text, and confirms no script flag | PASS |
| Malicious filename/path | basename validation plus object/signal-store containment tests; `../patient.edf` rejected | PASS |
| Oversized upload | server sums declared bytes against configured limit; boundary integration test returns `too_large` | PASS |
| Unsupported format | transport enum rejects unsupported format with safe validation response | PASS |
| Missing BrainVision companions | adapter test returns safe `missing_companion` detail | PASS |
| Malformed import | Chromium invalid CSV configuration shows “Import stopped safely,” reference ID, and recovery action | PASS |
| Session/OIDC tamper | signed-session tamper test; OIDC state/nonce/PKCE mocked exchange rejects wrong state | PASS |
| Origin/CSRF boundary | unsafe request with unapproved Origin returns `origin_forbidden`; cookies are HttpOnly/SameSite | PASS |
| Product telemetry leakage | property schema rejects extras; sentinels for filename, annotation, and review reason absent from event rows | PASS |
| Direct local object traversal | object and signal-store resolved-path containment tests | PASS |
| Signed S3 URL scope/expiry | code uses SDK presigning and private bucket contract; no live S3 provider exercised in this phase | NOT LIVE-VALIDATED |
| Parser process isolation | non-root worker Dockerfile and bounded formats; image/runtime sandbox not executed here | BLOCKED |
| Restart persistence | named volumes/shared storage designed; no Docker runtime | BLOCKED |

## Safe failures and support

Ingestion diagnostics log only opaque recording/job IDs, adapter, coarse format,
build version, failure class, diagnostic UUID, and sanitized traceback frames
(module basename, function, line). Exception messages, original paths, filenames,
headers, samples, credentials, and signed URLs are excluded. The user receives a
stable safe message and copyable error reference. Feedback never attaches a file,
signal, metadata, or annotation automatically.

## Residual risks and launch gates

- MNE and format dependencies parse complex untrusted bytes. Run workers with
  non-root users, CPU/RAM/time limits, no unnecessary egress, read-only image
  filesystem, isolated temporary directories, and a separate service identity.
- Live S3 CORS, public-access block, presigned method/key/expiry, IAM scope, and
  deletion need provider-specific integration tests.
- Shared-database authorization remains application-enforced; later database RLS
  is defense in depth, not a reason to weaken current checks.
- CSP is strict on API responses; the hosted web must also ship reviewed CSP/HSTS
  headers at its ingress.
- Rate limiting and abuse protection are deployment responsibilities and must be
  configured before broad distribution.
- Backup restore, physical deletion delay, incident response contacts, retention,
  privacy notice, and terms must be owned by a human operator before real data.
- The public alpha must prohibit identifiable/regulated health data and cannot
  claim HIPAA compliance.

Any known cross-tenant disclosure, public bucket/object, raw scientific payload in
analytics/logs, production dev-auth, unsafe parser escape, or failed authorization
test blocks release. Run dependency and container scans again on the immutable
release images.
