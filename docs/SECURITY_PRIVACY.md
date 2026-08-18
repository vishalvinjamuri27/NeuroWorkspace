# Security and privacy design

## Boundary

NeuroWorkspace is a non-clinical research/engineering tool. The alpha is not a
medical device and is not represented as HIPAA compliant. Hosted alpha policy is
public, synthetic, or appropriately deidentified research data only. Technical
controls reduce risk but do not create regulatory compliance.

Neural recordings and headers can be sensitive. Raw objects are private, encrypted
in transit and at rest by the configured provider, and never public bucket URLs.
Only authorized, short-lived signed access is issued. The normal UI receives an
allowlisted metadata projection; protected originals retain all source information
without exposing it casually.

## Data handling principles

- Never log signal samples, annotation/description text, source header contents,
  subject/patient names, birth dates, hospital identifiers, raw object keys,
  access tokens, or signed URLs.
- Never send user signals or metadata to external AI APIs and never train models
  on uploads. A future external processor is separately consented and opt-in.
- TLS is required outside local development. Storage/database encryption uses
  managed keys initially; higher-assurance customer-managed keys are future work.
- Secrets come from environment/secret managers, rotate by runbook, and never live
  in repository images or frontend variables.
- Operational analytics use coarse allowlisted values only. Free text is forbidden.
- Original and derived retention/deletion are visible to users. Temporary upload
  parts expire automatically; exports have configurable expiry.

## Access control

Authentication verifies a vendor-neutral identity; authorization evaluates active
workspace membership and a centralized role/action matrix on every resource and
object operation. Resource repository methods require workspace context. Denials
do not reveal whether an ID exists. UUIDs are defense-in-depth, not authorization.

Sessions use secure, HTTP-only, same-site cookies with CSRF protection for unsafe
methods. API tokens, if later added, are scoped, hashed at rest, expiring, and
audited. Invite and share tokens are random, single-purpose, expiring, revocable,
and stored hashed.

## Upload/parser isolation

File signatures and structure are checked; extension/MIME claims are insufficient.
Workers parse in isolated non-root containers with CPU, RAM, time, temp disk, file
count, and output limits, without cloud metadata access. Filenames become safe
basenames and object keys are generated. Companion references cannot escape the
working directory. Archives are off by default.

Parser errors return stable safe codes; tracebacks and potentially sensitive
metadata remain in restricted diagnostics. Dependency scanning and lockfiles limit
supply-chain exposure. Scientific parser updates require real-format regression
tests.

## Application security

- Input/output schemas reject unknown dangerous shapes and enforce size/range.
- React renders free text as text; no unsanitized HTML. CSV exports neutralize
  spreadsheet formula prefixes. Content-Disposition uses generated safe names.
- CORS is an explicit deployment allowlist. Security headers include CSP,
  frame-ancestors, nosniff, referrer policy, and permissions policy.
- Rate limits cover auth, upload signing, expensive waveform ranges, exports, and
  parser jobs by identity/workspace/IP as appropriate.
- Database queries are parameterized. Migrations run with a separate role;
  applications receive least privilege.
- Object keys are unguessable and responses never expose bucket topology.

## Deletion and retention

Deletion first makes the recording unavailable, then a retryable worker removes
source, canonical, derived, QC artifacts, exports, temporary versions, and caches.
A minimal audit tombstone stores opaque IDs/action/time, not scientific content.
Default operational logs retain 30 days; audit retention and backups are deployment
policy. Backup expiry means deletion is eventual there and must be disclosed.

## Incident readiness

Runbooks cover leaked credentials/signed URLs, cross-tenant authorization bugs,
malicious-file worker compromise, lost objects, and suspected sensitive logging.
Required abilities: revoke sessions/keys, stop worker queues, quarantine objects,
identify affected opaque IDs from audit trails, notify maintainers/users under the
applicable policy, remediate, and document lessons.

## User-facing notice

Every upload surface states: research/engineering use only; not for diagnosis or
patient care; do not upload identifiable or regulated data to an alpha deployment;
QC flags are automated review suggestions; users remain responsible for consent,
data rights, and scientific interpretation.

