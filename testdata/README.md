# Public EEG test data

No recordings are committed here. `manifest.yaml` describes checksum-pinned public
files and `scripts/fetch_test_data.py` downloads only selected samples into an
ignored cache. Dataset licenses and access terms remain authoritative at source.

Real-data acceptance requires an original upstream file and independent expected
facts. Generated files and format round trips are useful tests but never satisfy a
real-format matrix row. Subject/header strings must not appear in logs, reports, or
screenshots.

Until a candidate is downloaded and its current snapshot/checksum/metadata are
verified, its manifest status is `candidate`. The report must show BLOCKED rather
than a checkmark for any unverified row.

