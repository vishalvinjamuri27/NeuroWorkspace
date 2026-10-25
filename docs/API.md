# API semantics

The endpoint reference is generated, not written by hand:

- [`packages/shared-types/openapi.json`](../packages/shared-types/openapi.json)
  is the checked-in OpenAPI document, and `src/openapi.ts` beside it holds the
  TypeScript types the frontend uses. `make openapi` regenerates both, and CI
  fails if they drift from the running code.
- A running API serves the same document interactively at
  <http://localhost:8000/docs>.

This page covers the behavior the schema can't express. All routes are under
`/api`.

## Authorization

Every lookup, nested or top-level, checks workspace membership. A private
resource you can't access returns the same `404 not_found` as one that
doesn't exist, including when you're a member whose role lacks the
capability (for example, a viewer trying to annotate). Existence can't be
probed ([ADR 0005](adr/0005-authentication-authorization.md)).

## Errors

Every error uses one envelope:

```json
{"error": {"code": "missing_companion", "message": "The BrainVision header references subject01.eeg, but that file was not included.", "correlation_id": "...", "details": {}}}
```

`code` is stable and meant for branching; `details` holds only safe
correction hints. Internal exceptions and storage paths never appear;
unexpected failures are logged server-side under the correlation ID.

## Concurrency and retries

- **Optimistic concurrency.** Annotation `PATCH`/`DELETE` and bad-channel
  `PUT` send the client's last-seen `revision` (0 for a bad-channel row that
  doesn't exist yet). The server applies a single conditional
  `UPDATE ... WHERE revision = :revision`; a stale revision returns `409`
  (`annotation_revision_conflict` or `bad_channel_revision_conflict`), never a
  silent overwrite. Clients re-fetch and decide whether to reapply.
- **Upload completion** is a single conditional transition; completing an
  upload that's already completed or aborted returns `409 upload_not_open`.
- **Idempotency-Key.** `POST /recordings/{id}/processing-runs` accepts an
  `Idempotency-Key` header so retries don't start duplicate runs.

## Jobs

Ingestion, processing, and deletion run as jobs. `GET /jobs/{id}` reports
`status` (`QUEUED`, `RUNNING`, `CANCEL_REQUESTED`, `SUCCEEDED`, `FAILED`,
`CANCELLED`), `stage`, and `progress`. `POST /jobs/{id}/cancel` cancels a
`QUEUED` job immediately; a `RUNNING` job moves to `CANCEL_REQUESTED` and is
finalized at its next cooperative checkpoint.

A successful processing run repoints the recording's `active_version_id` at
the new derived version. The input version is never modified; each run in
`GET /recordings/{id}/processing-runs` records its `input_version_id` and
`output_version_id`.

## Pagination

Recording and annotation lists take `limit` and an opaque `cursor` and return
`items` plus `next_cursor` (`null` on the last page).

## Waveform

`GET /recordings/{id}/waveform` takes `start_seconds`, `duration_seconds`,
repeated `channel_index`, `pixel_width`, and optionally `version_id`. It
returns one bounded window: the actual interval, pyramid level and bin size,
and per-channel min/max envelopes with units. Channel count and pixel width
are capped; exceeding a cap returns a corrective `422`.

Responses carry a strong ETag. Pin `version_id` (the recording's
`active_version_id`) to get an immutable, cacheable response; a pin to a
version that's no longer active returns `409 version_changed`. Unpinned
requests are served `no-cache` and revalidate, because processing changes
which version the recording URL resolves to.

## Compatibility

Adding optional fields is non-breaking. Removing or renaming fields, or
changing units, interval semantics, or role behavior, is breaking.
