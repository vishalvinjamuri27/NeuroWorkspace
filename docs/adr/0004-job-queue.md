# ADR 0004: PostgreSQL job record with Celery/Redis delivery

## Context

Ingestion, QC, processing, export, and deletion outlive requests and need progress,
retry, cancellation, and auditability. A broker message alone is not product state.

## Decision

Store authoritative job state/idempotency/leases in PostgreSQL. Use Celery 5.6 with
Redis for delivery and retry. Tasks receive only opaque IDs, acquire a lease,
recheck desired state, write immutable temporary output, and publish atomically.
Tests may execute the same task use case eagerly.

## Alternatives

FastAPI background tasks share web lifecycle and are unsuitable. A Postgres-only
polling queue reduces dependencies but requires reliable lease/scheduling work.
Kafka/Kubernetes workflows are unnecessary. Other Python queues remain viable if
Celery operational burden proves high.

## Consequences

Celery is mature and workers scale independently, while database state supports UX
and recovery. Redis loss may redeliver or delay work but cannot lose scientific
truth. Idempotency, visibility timeout, acknowledgment behavior, and reconciliation
must be tested.

