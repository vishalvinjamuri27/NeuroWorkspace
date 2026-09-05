# Observability and product analytics

## Implementation status

This document describes the target observability surface; the sections below
predate the metrics implementation and describe more than currently exists.
What is actually built and tested today:

- **Metrics**: `neuro_workspace.metrics` exposes Prometheus counters/histograms
  at `GET /metrics` (unauthenticated, matching standard scrape practice) --
  `neuroworkspace_http_requests_total`/`_http_request_duration_seconds` by
  route template and method; `neuroworkspace_jobs_started_total`,
  `_jobs_succeeded_total`, `_jobs_failed_total` by job kind;
  `neuroworkspace_job_leases_reclaimed_total`,
  `_jobs_dead_lettered_total`, `_jobs_redispatched_total` for the outbox
  dispatcher/lease-reclaim sweep (`services.jobs.run_dispatch_sweep`); and
  `neuroworkspace_cache_requests_total` by cache name and hit/miss/error
  outcome. Tests: `tests/python/test_metrics.py`. `docker-compose.yml`'s
  `observability` profile (`docker compose --profile observability up`) runs
  Prometheus scraping this endpoint using `observability/prometheus.yml`; no
  Grafana dashboard is checked in (see the compose file comment for why).
  Only the API process is scraped -- the Celery worker has no HTTP server to
  scrape metrics from in this implementation; a `celery-exporter`-style
  sidecar would be needed to cover worker-side metrics and is not yet added.
- **Structured logging**: implemented via `structlog` with correlation IDs,
  route, and safe error codes on the request-boundary middleware and every
  job executor (see `services.jobs._error_report`), not the full field list
  below.
- **Not yet implemented**: OpenTelemetry tracing, the full metrics list
  below, SLO/alerting configuration, and the product-analytics abstraction
  described here are targets, not current code -- do not assume they exist
  without checking the corresponding module.

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

