# Threat model

Method: asset/trust-boundary review with STRIDE-style scenarios. Scope is the V1
web, API, workers, database, cache/queue, object storage, browser, CI artifacts,
and external identity provider boundary.

## Assets and actors

Critical assets are original recordings, derived signals, sensitive headers,
annotations, membership, credentials, signed URLs, audit integrity, provenance,
and service availability. Actors include legitimate users, malicious tenant users,
unauthenticated attackers, compromised dependencies/files, operators, and mistakes.

## Trust boundaries

1. browser to public web/API;
2. API to identity provider;
3. API/worker to Postgres, Redis, and object storage;
4. untrusted uploaded bytes into parser workers;
5. CI/build dependencies into deployable images;
6. tenant/workspace boundaries within shared infrastructure.

## Primary scenarios and controls

| Threat | Impact | Prevent/detect controls | Verification |
|---|---|---|---|
| Guess another recording ID (IDOR) | Cross-tenant disclosure | membership-scoped repositories, uniform 404, signed URLs only after policy | two-user API and object tests |
| Reuse leaked signed URL | Raw data disclosure | short expiry, method/object/part scope, TLS, no logs/referrers, revocation via key/path deletion | signature/expiry tests |
| Path or companion traversal | Worker/other-file read | generated working dir, safe basename, resolved-path containment, no symlinks | malicious BrainVision/EEGLAB fixtures |
| Zip slip/bomb | overwrite/exhaustion | archives disabled P0; bounded inventory/ratio/bytes/files when added | adversarial archive tests |
| Malformed scientific file | crash/RCE/exhaustion | isolated non-root worker, no metadata network, resource limits, parser pinning, timeouts | fuzz corpus and worker-kill tests |
| Oversized upload/job flood | cost/availability | pre-sign quota, part/total limit, rate limit, queue backpressure, per-workspace concurrency | load/rate tests |
| XSS in annotation/metadata | session theft | schema length, text rendering, CSP, no HTML, safe CSV | payload E2E/security tests |
| Formula injection in export | workstation execution | prefix dangerous cells, quote consistently, warning | export unit tests |
| Stale/deleted job publishes | data resurrection | tombstone and desired-state check before atomic publish | deletion race integration test |
| Duplicate task corrupts state | provenance loss | idempotency keys, lease, immutable temp prefix, manifest hash, atomic pointer | forced redelivery test |
| Sensitive telemetry | privacy breach | structured allowlist, log filters, bounded metrics labels, review | capture-and-scan tests |
| Queue/object credentials leak | broad compromise | secret manager, least privilege, rotation, environment isolation | config audit/runbook drill |
| Dependency compromise | code execution | lockfiles/hashes, trusted registries, Dependabot/scans, minimal images | CI audit and image scan |
| CSRF/session fixation | unauthorized writes | SameSite+secure cookies, CSRF/origin checks, rotation on login | API security tests |
| Brute force/auth abuse | account takeover | external OIDC protections or dev-only auth, rate limiting, generic errors | rate tests |
| Object/database loss | research loss | versioning policy, checksums, PITR, restore drills, immutable provenance | scheduled restore test |

## Sensitive metadata leakage paths

Headers, filenames, annotation labels, URLs, exception strings, metrics labels,
analytics, browser error reporting, exports, and screenshots are reviewed. The safe
metadata mapper is allowlist-only. Parser exceptions are translated. Product
analytics never accept arbitrary properties. Support bundles require user action
and show exact contents.

## Residual risks before alpha

- Mature scientific parsers still process hostile complex inputs; container
  isolation is necessary but cannot prove safety.
- Local Compose cannot replicate cloud IAM or network boundaries.
- A shared-database application bug can cross tenants despite policy design;
  authorization tests and later database RLS are defense-in-depth priorities.
- Browser-visible waveforms can be captured by an authorized endpoint/user.
- Backups delay physical deletion according to retention.

## Release gates

No alpha release with a known cross-tenant disclosure, unsigned/public object,
raw sample/header in logs, traversal escape, disabled authorization test, default
production dev-auth, or unbounded archive extraction. Security defects take
precedence over feature count.

