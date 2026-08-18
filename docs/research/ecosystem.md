# Neurophysiology ecosystem research

Status: completed for the architecture gate on 2026-08-18. Sources are primary
documentation, specifications, package registries, and project repositories.

## Conclusions

NeuroWorkspace should be a workflow and collaboration layer around mature
scientific I/O, not a new binary-format library. MNE-Python 1.12.1 supplies lazy
readers for EDF/EDF+, BDF, BrainVision, EEGLAB, FIF, and many future formats.
MNE-BIDS 0.19.0 supplies BIDS path semantics plus read/write helpers. Adapters
will isolate MNE quirks and normalize units, time, events, and companion files.

The browser must receive viewport-sized envelopes rather than recordings. The
canonical store will use Zarr v3 arrays with channel-major/time-chunked data and
separate min/max pyramid levels. Zarr is selected because it has an open format,
Python support, independently readable compressed chunks, object-store support,
and v3 sharding. The API—not the browser—owns Zarr access so storage layout may
evolve without breaking clients.

Canvas 2D is the first renderer. At the planned limit of roughly two extrema per
horizontal pixel per visible channel, its simplicity, text integration, and broad
support win. The renderer boundary accepts typed arrays and can gain a WebGL
implementation if measured Canvas frame time exceeds the budget. SVG/DOM marks
are rejected because their cost grows with every displayed point.

## Scientific I/O and standards

### MNE-Python and MNE-BIDS

- MNE-Python's generic `read_raw` and format-specific readers support all P0
  binary formats. `preload=False` is essential for bounded-memory inspection and
  slicing. It may upsample mixed-rate EDF/BDF signals to the highest requested
  rate, so the source rates and this normalization must be recorded.
- MNE returns physical values in SI units. The normalized model stores the unit,
  source unit, and conversion factor; APIs display human-friendly units without
  silently changing provenance.
- EDF+ annotations map to MNE annotations. BrainVision markers and EEGLAB events
  also require format-specific normalization tests.
- MNE-BIDS reads and writes BIDS datasets and preserves the directory/entity
  model. When conversion is necessary it may choose BrainVision for EEG. Exports
  therefore must report whether bytes were copied or converted and what could not
  be preserved.

Sources: [MNE raw readers](https://mne.tools/stable/api/reading_raw_data.html),
[MNE EEG I/O notes](https://mne.tools/stable/auto_tutorials/io/20_reading_eeg_data.html),
[MNE-BIDS](https://mne.tools/mne-bids/stable/), and
[MNE-BIDS write semantics](https://mne.tools/mne-bids/stable/generated/mne_bids.write_raw_bids.html).

### BIDS EEG

BIDS 1.11.1 is the current stable specification observed during research. EEG
data uses subject/session/task entities, a raw recording plus an EEG JSON sidecar,
and usually channels/events/electrodes tables. Required or recommended metadata
includes sampling frequency, power-line frequency, reference, channel name/type/
unit, and coordinate-system details. Accepted EEG data formats include EDF,
BrainVision, EEGLAB, and BDF, with EDF and BrainVision preferred by the standard.

NeuroWorkspace stores BIDS entity names in a namespaced metadata object, keeps
sidecars as immutable source assets, and does not flatten electrodes into channels.

Source: [BIDS stable specification](https://bids-specification.readthedocs.io/en/stable/)
and [BIDS entity table](https://bids-specification.readthedocs.io/en/stable/appendices/entity-table.html).

### EDF / EDF+

EDF is a header plus fixed-duration data records and permits per-signal physical
ranges and samples per record. EDF+ adds annotations and discontinuous records.
Headers can contain identifying fields. The adapter must inspect but not expose
patient/recording identifiers, handle differing channel sample rates deliberately,
and retain EDF+ discontinuities and annotations rather than pretending all data is
uniformly continuous.

Sources: [EDF specification](https://www.edfplus.info/specs/edf.html) and
[EDF+ specification](https://www.edfplus.info/specs/edfplus.html).

### BioSemi BDF

BDF is a 24-bit EDF-family format; its status channel and BioSemi reference model
need explicit handling. Reading must never silently apply an EEG reference. The
status word event mask is format-specific and will be tested. Source:
[BioSemi file format](https://www.biosemi.com/faq/file_format.htm).

### BrainVision

A recording is a linked `.vhdr`, `.vmrk`, and `.eeg` set. The two text files may
reference companions by name. The upload boundary groups these assets, rejects
missing or duplicate companions, normalizes safe basenames, and never renames one
without rewriting references in a protected working copy. Source:
[BrainVision Core Data Format](https://www.brainproducts.com/download/specification-of-brainvision-core-data-format-1-0/).

### EEGLAB `.set`

EEGLAB datasets may contain MATLAB-encoded samples or point to an external `.fdt`.
The group validator resolves only same-upload basenames, blocks traversal, and
gives an actionable missing-companion error. The upstream format is flexible, so
real files—not only generated fixtures—are mandatory. Source:
[EEGLAB data import/export documentation](https://eeglab.org/tutorials/misc/Exporting_Data.html).

### FIF

FIF is MNE's native container and can carry rich measurement metadata and
annotations. It is supported for import and derived export, but is not the web
storage format because its chunk/range behavior is not object-store-native.

## Public data repositories

- PhysioNet Sleep-EDF Expanded 1.0.0 contains 197 whole-night PSG recordings and
  annotations. We fetch one cassette PSG plus its hypnogram and verify published
  SHA-256 hashes. [Dataset](https://physionet.org/content/sleep-edfx/1.0.0/).
- OpenNeuro publishes versioned BIDS snapshots and offers individual files,
  GraphQL, CLI, git-annex, and public S3 access. We pin snapshots and fetch only a
  minimal subject/session subset. [Download documentation](https://docs.openneuro.org/user_guide.html).
- MNE's test/sample datasets are suitable for independently produced FIF and
  selected BDF fixtures when their license and upstream checksum are recorded.

Candidate IDs from the mission (ds006547 BrainVision, ds004362 EEGLAB, ds002578
BDF) are treated as leads, not facts. The fetch script must verify current
snapshot contents before a matrix entry can pass.

## Existing workflows and product gap

MNE's browser and MNELAB provide capable local scientific inspection. EEGLAB's
scrolling viewer supports scale, channel count, zoom, and manual rejection while
its command history supports reproducibility. OpenNeuro excels at public BIDS
distribution. These tools validate core interactions but leave a gap for a
web-first, multi-format, collaborative workspace with immutable sources,
server-side large-data tiling, attributed annotations, and reproducible jobs.

Sources: [MNELAB](https://mnelab.readthedocs.io/en/stable/) and
[EEGLAB scrolling data](https://eeglab.org/tutorials/06_RejectArtifacts/Scrolling_data.html).

## Storage alternatives

| Choice | Random access | Object storage | Python/science | Decision |
|---|---|---|---|---|
| Zarr v3 | Native n-D chunks; independent inner chunks with sharding | Key/value and fsspec backends; tune shards to avoid object explosion | Strong NumPy/Dask/Xarray ecosystem | Selected behind an abstraction |
| HDF5 | Chunked | Poor direct object-store mutation/range semantics; usually needs a file proxy | Very mature | Export/interchange only |
| Parquet | Excellent tabular column pruning | Excellent | Mature | Metadata/events, not dense waveform arrays |
| TileDB | Strong sparse/dense arrays and cloud design | Strong | Mature but adds a specialized engine and operational surface | Reconsider if Zarr indexing/transactions become limiting |
| Custom binary tiles | Can be optimal | Easy ranges | Product-owned format and migration burden | Rejected for V1 |

Zarr's v3 sharding groups many independently compressed chunks into fewer storage
objects, but full shards are the efficient write unit. We therefore write each
derived recording once, consolidate metadata, and expose only immutable versions.
Source: [Zarr arrays and sharding](https://zarr.readthedocs.io/en/stable/user-guide/arrays/).

## Viewer delivery and rendering

For a viewport of width `W`, the API targets no more than `2W` values per channel.
It chooses the coarsest pyramid level whose bin count is at least `W`, preserving
the minimum and maximum in every source interval. Responses are binary-capable,
cacheable by recording version and viewport, and JSON in the initial API for
debuggability. Neighbor windows are prefetched client-side with cancellation.

Canvas is benchmarked before adopting GPU complexity. WebGL/WebGPU remain credible
for hundreds of simultaneous channels; deck.gl demonstrates the scaling value of
GPU buffers but is geospatially oriented and not selected as the waveform engine.
Source: [deck.gl performance guidance](https://deck.gl/docs/developer-guide/performance).

## Infrastructure versions checked

Registry and official documentation checks on 2026-08-18 found MNE 1.12.1,
MNE-BIDS 0.19.0, FastAPI 0.141.1, Zarr 3.3.0, Next.js 16.3.1, React 19.2.8,
and PostgreSQL 18.4. The repository lockfiles, not this table, are authoritative
for reproducible builds. We choose Python 3.12 for broad scientific-wheel support,
Node 20 LTS-compatible runtime, and PostgreSQL 18.

Sources: [Next.js installation](https://nextjs.org/docs/app/getting-started/installation),
[PostgreSQL version policy](https://www.postgresql.org/support/versioning/), and
[Celery task semantics](https://docs.celeryq.dev/en/stable/userguide/tasks.html).

## Risks to validate

1. Mixed-rate EDF/BDF channels and discontinuous EDF+ timebases.
2. Zarr v3 object-store behavior and metadata consistency under failed ingestion.
3. MNE readers that require preload or companion-file locality.
4. Canvas performance at 256 visible channels and high-DPI displays.
5. Dataset candidates changing snapshots or licensing/access terms.

