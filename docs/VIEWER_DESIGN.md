# Viewer design

## Experience

The viewer is the product center, not a generic chart. A fixed top bar identifies
project/recording and readiness. A virtualized channel rail controls visibility,
groups, bad state, and scale. The Canvas waveform fills the central surface. A
bottom inspector switches among events, annotations, QC, and safe metadata.

The sample path opens directly into this layout. Loading keeps axes and channel
labels stable, shows a subtle viewport skeleton, and cancels obsolete requests.
Errors preserve the prior viewport and offer retry.

## View state

URL-safe state includes recording/version, start/duration, selected channels,
global/per-channel scale mode, and inspector tab. Ephemeral cursor/drag state is
local. The server persists user recording preferences separately after a debounce.

Constraints clamp to the recording range and a minimum window based on sample
rate. Pan keeps duration fixed. Zoom anchors at pointer/playhead. Jump accepts
seconds and `hh:mm:ss.sss`. Keyboard commands have button equivalents:

- left/right pan 10%; shift pans one viewport;
- plus/minus zoom around playhead;
- home/end jump; `g` focuses jump; `r` resets; `a` starts an annotation selection;
- escape cancels the active gesture.

## Request protocol

`GET /recordings/{id}/waveform` requires version, start, duration, channel IDs,
pixel width, and response format. The API returns actual bounds, sample rate,
chosen level/bin width, per-channel unit/scale hints, and arrays of bin times,
minimum, maximum, and validity. Limits reject huge channel/pixel combinations
with a suggested smaller request.

Keys include immutable version, sorted channel indexes, quantized bounds, level,
and representation version. The client maintains an LRU by byte size, coalesces
identical requests, aborts stale fetches, and prefetches one adjacent viewport only
after the visible request settles. HTTP ETags permit revalidation. A future binary
encoding preserves the same semantic contract.

## Canvas renderer

One high-DPI Canvas draws grid, per-channel baseline/labels, extrema envelopes,
events/annotations, cursor, and selection in layered passes. Channel rows are
virtualized; only visible rows are drawn. Every bin draws a vertical min/max line;
at raw resolution adjacent samples may be connected. There are no per-point DOM
nodes. Text labels outside the waveform use semantic HTML.

Rendering accepts an immutable typed view model and has no network/domain logic.
A requestAnimationFrame scheduler collapses intermediate pointer updates. Resize
uses `ResizeObserver`. Device-pixel ratio is capped when measured fill rate would
miss the frame budget. OffscreenCanvas/WebGL are reserved behind the renderer
interface, not assumed.

Benchmark gates on a typical modern laptop:

- 64 visible channels, 1,920 CSS px: p95 draw below 16 ms target, 32 ms ceiling;
- 128 and 256 channel stress views: interactions remain responsive via row
  virtualization and point cap;
- cached pan begins within one animation frame;
- heap remains bounded during 100 alternating pan/zoom actions.

Results belong in the alpha report; targets are not results.

## Scaling and scientific meaning

Raw canonical values are SI units. Display defaults use type-aware engineering
prefixes (for example microvolts for EEG) and show the unit. Global scale means one
physical amplitude per channel type; per-channel auto uses a robust visible-window
range and is visibly marked `auto`. Neither changes data. Zero/baseline position
and inverted channel conventions are consistent.

Envelope min/max preserves visible extremes but is not a reconstruction of the
signal. The UI says `min/max envelope` at reduced resolution and provides current
bin width. QC/annotations use source-time coordinates independent of pyramid.

## Annotation interactions

Dragging the plot selects `[onset, duration]`; click creates a point annotation.
The form requires a label, allows description, and shows exact times. Saving adds
an optimistic overlay, then reconciles the authoritative revision. Edit/delete
requires the latest revision; conflict UX offers reload, not silent overwrite.
Imported events are read-only. User annotations and algorithmic flags have
visually distinct shapes/colors and textual legends.

Bad-channel actions are explicit per channel and reversible. Confirming a QC flag
does not silently mutate the raw source or other recording versions.

## Accessibility and fallback

The canvas has an accessible name and live textual summary: time range, visible
channel count, resolution, selection, and cursor. A table exposes visible-channel
min/max and QC state for users who cannot interpret the trace. Controls use native
buttons/inputs, focus is never drawn inside Canvas only, colors are not the sole
status signal, and shortcuts are disabled while typing.

## Benchmark implementation

A deterministic generated-data page and Playwright harness measure fetch, decode,
draw, interaction, and heap for mission scenarios A-D without allocating the full
largest recording in the browser. Backend stress generation streams directly into
the canonical store. Long-task observations and browser trace artifacts remain
untracked by default.

