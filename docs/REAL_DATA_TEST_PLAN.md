# Real-data test plan

## Dataset policy

Only public, openly accessible files with recorded license/access terms are fetched.
Large or subject-containing data remains ignored and local. Downloads are minimal,
resumable where the server permits, checksum-verified, and pinned to versioned
dataset paths/snapshots. The repository contains only manifest metadata and code.

## Required candidates and verification

| Capability | Candidate | Independent acceptance |
|---|---|---|
| EDF/EDF+ | PhysioNet Sleep-EDF Expanded cassette PSG plus hypnogram | header/detection, channel rates/units, duration, windows, annotations, QC, viewer, export |
| BrainVision + BIDS | OpenNeuro ds006547 minimal subject/session | `.vhdr` resolves downloaded `.vmrk` and `.eeg`; markers/sidecars/events agree |
| EEGLAB | OpenNeuro ds004362 minimal recording | determine embedded versus external `.fdt`; events and physical values agree |
| BDF | verified public BioSemi source (investigate ds002578 or MNE test source) | original upstream `.bdf` hash; status/events; no EDF-generated substitute |
| FIF | official MNE sample/testing EEG raw file | FIFF metadata, lazy windows, annotations where present |
| BIDS | same minimal OpenNeuro BrainVision or another small EEG dataset | official validator, discovery, entities, JSON/channels/events/electrodes |

Candidate IDs are rechecked before pinning. A candidate that is missing, gated,
not the expected format, or unclearly licensed is replaced with another primary
source and the manifest records why.

## Procedure

1. `scripts/fetch_test_data.py list` prints URLs, sizes, terms, and cache state.
2. `fetch --sample <id>` downloads to `.cache/neuro-workspace/testdata`, verifies
   the manifest SHA-256, and refuses mismatches.
3. `inspect` runs the adapter without canonical conversion and emits safe metadata.
4. `pytest -m real_data --real-data-root ...` ingests into a temporary store and
   performs metadata/window/event/QC/export assertions.
5. `scripts/real_data_report.py` writes a machine-readable result and Markdown
   table with pass/fail/blocked per capability. The alpha report copies this output.

## Expected-value rules

Expected channel count, sampling rates, duration, and event count come from the
specific pinned file plus authoritative metadata. Tolerances are explicit. We
manually inspect one window with the source MNE reader and compare multiple random
and boundary windows automatically. Values derived only from our adapter are not
independent expectations.

## CI schedule

PR CI runs tiny redistributable/generated fixtures and never labels them real-data
acceptance. A manual/scheduled workflow restores a checksum-addressed cache, fetches
missing public files, runs the matrix, uploads reports (not recordings), and fails
on regression. Dataset server outages mark infrastructure-blocked only after hash-
verified cache is unavailable.

## Privacy

The test cache is access-restricted and ignored. UI screenshots use a synthetic or
explicitly public sample with attribution and no displayed subject fields. Logs and
reports contain sample IDs and safe recording facts, not header subject strings.

