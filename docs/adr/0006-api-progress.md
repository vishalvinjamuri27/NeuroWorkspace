# ADR 0006: Poll job progress before realtime infrastructure

## Context

Uploads and scientific jobs need progress after a browser reconnects. SSE or
WebSockets can improve immediacy but add connection, proxy, auth, and scaling work.

## Decision

Use resumable direct uploads and bounded polling of durable job resources with
ETag and `Retry-After`. The web backs off when backgrounded and stops at terminal
state. Add SSE only if measured polling load or user experience warrants it.

## Alternatives

WebSockets provide bidirectional updates we do not need. SSE is simpler but still
introduces long-lived deployment behavior. Broker messages cannot be exposed to
clients or used as authoritative state.

## Consequences

V1 operates across simple hosts and reconnects cleanly. Progress may lag by a few
seconds and API read load must be monitored. A future SSE endpoint can stream the
same persisted job state without changing jobs.

