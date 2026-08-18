# Observability and product analytics

## Operational signals

All processes write JSON logs with timestamp, severity, service/build/environment,
request or job correlation ID, route/task name, opaque workspace/recording/job ID,
safe error code, and duration. A redaction/filter layer rejects known sensitive
keys and truncates values. No signal values, subject/header values, annotation text,
filenames, raw keys, tokens, or signed URLs are allowed.

OpenTelemetry-compatible traces cover browser request (where consented), API
authorization/query, enqueue, worker stages, object reads/writes, and waveform
serving. Trace sampling increases for failures but never adds scientific payload.

## Metrics

- API requests/errors/latency by normalized route and status;
- waveform p50/p95 latency, cache hit, response bytes, channel count bucket,
  resolution level, and rejection count;
- jobs queued/running/age/attempts/failures by bounded type/error/format;
- ingestion duration, source bytes, peak RSS, canonical bytes, parse failures;
- upload completion/abort/integrity failure and export failure;
- object/DB/Redis latency/error and worker heartbeat;
- deletion backlog/age and orphan temporary prefixes.

Labels never include user input, filenames, annotation labels, dataset subject,
signed URL, or full resource IDs. Exemplars use trace IDs.

## SLOs and alerts

Initial targets: API availability 99.5% for hosted alpha, waveform p95 under 500 ms
for defined viewport, successful ingestion excluding validated user-file errors over
99%, and deletion jobs completed within 24 h. Alerts target sustained user impact:
error-budget burn, queue oldest age, repeated worker OOM/crash, object/DB failures,
authorization anomaly, and deletion backlog—not individual noisy failures.

## Safe failure taxonomy

Error codes distinguish user-correctable validation, unsupported input, transient
infrastructure, internal defect, cancellation, quota, and authorization. Internal
diagnostic IDs link restricted traces/logs. User messages contain action and a safe
basename only when needed.

## Product analytics abstraction

Domain code calls an allowlisted event sink. Disabled/local sink is default; hosted
operators may configure a privacy-reviewed vendor. Events include mission funnel
events from signup/workspace/project/sample/upload through viewer/QC/annotation/
processing/export/invite. Properties are event-specific enums/coarse buckets.

Schemas reject arbitrary payload properties and explicitly forbid samples, header
metadata, identifiers, filenames, annotation contents, PHI, or URLs. Admin metrics
are aggregate queries with minimum cohort sizes where needed: signups, active users,
uploads, ingest success/error by coarse format, viewer opens, annotations, repeat
use, exports, and invites.

## Runbooks and validation

Runbooks cover queue backlog, parser regression by format, high waveform latency,
object errors, DB saturation, deletion backlog, and sensitive-log incident. Tests
capture logs/analytics for adversarial metadata and scan for sentinel secrets and
payload values.

