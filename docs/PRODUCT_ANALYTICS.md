# Product analytics

## Purpose and boundary

Product evidence exists to decide whether EEG users reach value and return. It is
not a behavioral surveillance feed, scientific dataset, or substitute for the
security audit trail. Events are stored in the application database and exposed
only through an allowlisted admin endpoint. No third-party analytics vendor is
required.

Never add raw samples, filenames, source/header metadata, patient or subject
fields, annotation text, channel names, clinical notes, file paths, signed URLs,
tokens, email addresses, or user-provided free text to a product event. Event
properties are rejected unless both the event and property are allowlisted in
`product_analytics.py`.

## Event contract

All events contain a generated event ID and timestamp. Where applicable they may
contain opaque internal actor, workspace, and recording IDs plus a random
browser-session ID. Server-side events are authoritative for mutations and
outcomes. The browser emits only a small set of meaningful viewer events once per
session/recording, not pointer or wheel streams.

| Event | Emitted by | Allowed properties |
|---|---|---|
| `sample_demo_started` | browser | none |
| `sample_recording_opened` | browser after load | none |
| `signup_completed` | server on first identity | none |
| `workspace_created` | server | none |
| `project_created` | server | none |
| `upload_started` | server | `format`, `size_bucket` |
| `upload_completed` | server | `format`, `size_bucket` |
| `upload_failed` | server validation/upload boundary | `format`, `size_bucket`, `failure_class` |
| `ingestion_started` | worker | `format`, `size_bucket` |
| `ingestion_succeeded` | worker | `format`, `size_bucket` |
| `ingestion_failed` | worker | `format`, `size_bucket`, `failure_class` |
| `recording_opened` | browser after owned recording load | `source=owned` |
| `recording_reopened` | server when the same user opens it in a later browser session | `source=owned` |
| `viewer_zoomed`, `viewer_panned`, `channel_visibility_changed`, `qc_opened` | browser once per session/recording | none |
| `annotation_created`, `annotation_updated`, `annotation_deleted` | server | none |
| `bad_channel_marked` | server | none |
| `processing_run_created` | server | allowlisted `operation_id` |
| `export_started`, `export_completed`, `export_failed` | server | export `format`; failures also include class |

Format is one of `edf`, `bdf`, `brainvision`, `eeglab`, `fif`, `csv`,
`synthetic`, or `unknown`. File size is reduced to `<100 MB`, `100-500 MB`,
`500 MB-2 GB`, `2-10 GB`, `>10 GB`, or `unknown`.

The shared demo does not emit upload/ingestion events. Collaboration invite events
are not claimed because the current alpha has membership administration but no
invite lifecycle.

## Querying metrics

`GET /api/admin/product-metrics` requires a signed-in user whose normalized email
is in `ALPHA_ADMIN_EMAILS`. The route returns 404 to everyone else and never
returns event rows or user content. `GET /api/admin/feedback` uses the same gate.

The response includes counts by event, unique actor counts, safe format/size
aggregates, and:

- **activation:** users who opened any owned recording / new users;
- **upload activation:** users with successful ingestion / users starting upload;
- **annotation usage:** users creating an annotation / users opening an owned recording;
- **return behavior:** users reopening a recording in a later browser session /
  users opening an owned recording;
- **multi-recording behavior:** users with at least two successful ingestions /
  users with at least one successful ingestion.

Rates are `null` when their denominator is zero. “Return” currently means a later
random browser session, not a calendar day. That keeps the first alpha useful
without adding an IP address, device fingerprint, or third-party identity graph.

## Retention and operations

Treat product events as personal data because opaque IDs can be joined internally.
Restrict the admin endpoint, database, and backups; establish a documented
retention period before launch; delete or aggregate old rows according to the
published privacy notice. Do not grant analytics access to the general application
role in a future warehouse.

Before extending the schema:

1. state the product question;
2. prove existing aggregate data cannot answer it;
3. add an enum or coarse bucket, never arbitrary text;
4. test a payload containing sentinel filename/annotation/subject values;
5. update this document and the privacy notice.

The integration suite verifies that extra client properties are rejected and that
filename, annotation, and bad-channel reason sentinels do not enter event
properties.
