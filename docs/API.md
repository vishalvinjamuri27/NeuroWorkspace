# API contract overview

FastAPI's checked-in OpenAPI document is the executable specification. This page
records compatibility and resource semantics; examples omit authentication headers.

## Resources

```text
POST   /api/session/dev
GET    /api/session
GET/POST /api/workspaces
GET/POST /api/workspaces/{workspace_id}/members
GET/POST /api/workspaces/{workspace_id}/projects
POST   /api/projects/{project_id}/uploads
POST   /api/uploads/{upload_id}/parts
POST   /api/uploads/{upload_id}/complete
GET/POST /api/projects/{project_id}/recordings
GET    /api/recordings/{recording_id}
DELETE /api/recordings/{recording_id}
GET    /api/recordings/{recording_id}/channels
GET    /api/recordings/{recording_id}/waveform
GET    /api/recordings/{recording_id}/events
GET/POST /api/recordings/{recording_id}/annotations
PATCH/DELETE /api/annotations/{annotation_id}
GET/PUT /api/recordings/{recording_id}/bad-channels/{channel_id}
GET    /api/recordings/{recording_id}/qc
GET/POST /api/recordings/{recording_id}/processing-runs
GET/POST /api/recordings/{recording_id}/exports
GET    /api/jobs/{job_id}
```

All nested and top-level lookups authorize workspace membership. Missing and
unauthorized private resources return the same response. Mutation retries use
`Idempotency-Key` where creation is not naturally idempotent. Annotation PATCH/
DELETE requires `If-Match` revision.

Lists use `limit` and opaque `cursor` and return `items` plus `next_cursor`.
Errors use a stable shape:

```json
{"error":{"code":"missing_companion","message":"The BrainVision header references subject01.eeg, but that file was not included.","correlation_id":"...","details":{}}}
```

`details` contains only safe structured correction hints. Internal exceptions and
storage paths never appear.

## Waveform

Inputs are `version_id`, `start_seconds`, `duration_seconds`, repeated `channel_id`,
and `pixel_width`. Bounds are half-open in sample space. The response includes the
actual interval, level/bin size, resolution label, and per-channel `minimum`,
`maximum`, `valid_count`, unit, and display scale. The server caps channels, pixels,
points, duration, and response bytes and returns a corrective 422 if exceeded.

Immutable version responses carry ETag and private cache directives. Preview
processing is an explicit request parameter set and a different cache key; no
preview can create persisted data.

## Compatibility

Additive optional fields are nonbreaking. Removing/renaming fields, changing units
or interval semantics, or changing role behavior is breaking. CI diffs OpenAPI and
generated types. A future `/api/v2` is used only when coexistence is necessary.

