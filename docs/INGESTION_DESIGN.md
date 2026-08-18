# Ingestion design

## Contract

Ingestion converts an immutable, checksummed upload group into a normalized,
versioned recording without loading the full recording into memory. It is
idempotent at `(recording_id, source_manifest_hash, pipeline_version)`.

```text
create upload -> signed multipart parts -> complete + verify -> create recording
-> enqueue -> sniff/group -> inspect safe metadata -> validate -> chunk convert
-> QC + extrema pyramid -> manifest validate -> publish version -> READY
```

The browser can close after completion. Polling reports coarse user stages and
bytes/items processed. Temporary object prefixes are never visible to readers.

## Uploads and integrity

The API receives proposed filenames, sizes, and roles; it returns upload IDs and
short-lived signed part URLs. Limits are enforced before signing and again after
completion. Completion validates part inventory, byte size, object-store checksum
where available, and a worker-computed SHA-256. Client checksums are hints, not
trust anchors. Cancellation aborts unfinished multipart uploads.

Safe names are Unicode-normalized basenames used only for grouping and display.
Object keys are generated UUIDs. Paths, MIME types, extensions, archive contents,
and internal references are untrusted. Archives are disabled for P0 except a
future bounded BIDS workflow with file-count, expanded-size, ratio, path, and
symlink limits.

## Detection and group validation

Detection combines magic/header bytes, extension, companion relationships, and a
bounded parser probe. Extension alone cannot pass. Ambiguity fails with choices.

- EDF: ASCII version/header structure and bounded MNE probe; EDF+ continuity and
  annotations recorded.
- BDF: 0xff/BIOSEMI-style header plus structure; status channel retained.
- BrainVision: exactly one chosen `.vhdr`; parse its same-directory data and
  marker references, reject traversal/absolute paths/missing/ambiguous assets.
- EEGLAB: `.set`; inspect MATLAB structure/reference and require the named `.fdt`
  if external. No arbitrary MATLAB evaluation.
- FIF: FIFF magic/probe and MNE reader.
- CSV/TSV: explicit wizard config; sniff delimiter/encoding within bounds, require
  stable numeric rows, unique channel names, monotonic finite time when used.

Useful error codes include `missing_companion`, `malformed_header`,
`unsupported_encoding`, `ambiguous_format`, `duplicate_channel_name`,
`inconsistent_timebase`, `csv_config_required`, `too_large`, and `parse_timeout`.
Messages name a safe basename only and include a corrective action.

## Adapter interface

```python
class RecordingAdapter(Protocol):
    format_id: str
    def detect(self, assets: Sequence[AssetProbe]) -> Detection: ...
    def validate(self, group: LocalAssetGroup, config: ImportConfig | None) -> None: ...
    def inspect_metadata(self, group: LocalAssetGroup, config: ImportConfig | None) -> RecordingMetadata: ...
    def iter_signal_chunks(self, group: LocalAssetGroup, selection: ChunkPlan, config: ImportConfig | None) -> Iterator[SignalChunk]: ...
    def read_window(self, group: LocalAssetGroup, channels: Sequence[int], start: int, stop: int) -> NDArray: ...
    def read_events(self, group: LocalAssetGroup) -> Sequence[SourceEvent]: ...
    def read_annotations(self, group: LocalAssetGroup) -> Sequence[SourceAnnotation]: ...
```

MNE adapters share a lazy-reader base but retain individual detection, companion,
event, and provenance logic. CSV is independent. Adapter outputs are SI-valued
float arrays plus explicit unit/conversion metadata. Duplicate source channel
names are deterministically disambiguated for internal use while preserving the
original and warning; exports disclose the change.

## Chunked conversion

The worker materializes source assets into an isolated, size-limited working
directory because scientific readers expect seekable local files. It opens MNE
with `preload=False`, iterates contiguous sample ranges, and writes float32 by
default. Chunk duration is computed from channel count to target 16 MiB of raw
array data with bounded min/max duration. Float64 is available only when a format
precision analysis shows float32 error exceeds documented tolerance.

Every chunk validates shape, finite counts, exact requested sample boundaries, and
unit metadata. Peak process RSS and throughput are recorded without filenames.
The source is never modified. Partial stores live below a job UUID and are removed
after success/final failure.

Mixed source rates are recorded per channel. V1 canonical raw arrays require a
shared grid; MNE's resampling/upsampling behavior is documented in the manifest.
A future multi-timebase store can split synchronized channel groups without
changing the recording/domain API.

## Pyramid and QC

Pyramid construction streams raw chunks and carries incomplete four-sample bins
between chunks. Each bin records finite min/max and a valid count, so all-NaN bins
are explicit. Higher levels combine lower levels and never reread raw data. Tests
inject extrema at every boundary and compare all bins to a direct reference.

Streaming QC accumulators compute finite/missing counts, mean/variance, extrema,
flat runs, repeated-limit counts, and discontinuity candidates. Frequency-domain
metrics operate on bounded representative windows and record coverage; they do not
claim whole-recording certainty when sampled.

## Publication and retry

The worker writes a manifest containing source hashes, adapter/pipeline/MNE/Zarr
versions, metadata hash, array shapes/dtypes/chunks, pyramid definitions, unit
conversions, warnings, event count, and QC rule versions. It reopens and samples
the store before one database transaction publishes the new version and changes
state to READY. A retry that sees an identical valid manifest reuses it; one that
sees a partial prefix cleans only that job-owned prefix.

Permanent parser/validation failures are not retried. Object/DB/network failures
use jittered exponential retries. Worker time/memory limits convert crashes to a
safe failure and preserve internal diagnostics. Cancellation is checked between
chunks and before publication.

## BIDS import path

P1 BIDS import first inventories a directory/zip without extraction hazards, runs
the official validator where feasible, discovers EEG recordings with MNE-BIDS,
and creates one logical recording per BIDS acquisition while retaining dataset and
sidecar provenance. Dataset-level subject metadata remains restricted. Partial
dataset import records exactly which files/entities were selected.

