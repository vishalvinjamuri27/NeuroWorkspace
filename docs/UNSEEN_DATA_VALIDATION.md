# Unseen real-data validation

Three real, public EEG/PSG recordings were downloaded specifically for this
adversarial browser-validation pass, verified against
[`testdata/manifest.yaml`](../testdata/manifest.yaml) and every existing
`sha256:` value referenced by the repository's real-data acceptance tests to
confirm none had ever previously appeared in this repository's fixtures.

None of these files, nor their hashes, are committed to git — they were
downloaded to a local scratch directory outside the repository and used only
against the locally running Docker Compose stack.

## Selection method

1. Listed every `sha256` value in `testdata/manifest.yaml` (the file the
   repository's own `real_data`-marked tests pin against).
2. Confirmed the manifest's only PhysioNet Sleep-EDF entry is
   `sleep-cassette/SC4001E0-PSG.edf` (subject 1, night 1).
3. Selected three different PhysioNet Sleep-EDF Expanded subjects/sessions
   not present in that manifest, downloaded each individually (not the full
   8+ GB dataset), and computed SHA-256 before use.
4. Confirmed by direct comparison that none of the three hashes below match
   any hash in the manifest or anywhere else searched in the repository.

## Recordings used

| # | Filename | Source | Size | Format | SHA-256 |
|---|---|---|---|---|---|
| 1 | `SC4002E0-PSG.edf` | [PhysioNet Sleep-EDF Database Expanded 1.0.0](https://physionet.org/content/sleep-edfx/1.0.0/), `sleep-cassette/SC4002E0-PSG.edf` | 51,621,248 bytes | EDF | `bf91934cdbf7c9577bc91b411462ddb51c418e17e10a2f59a15feeeb917b0a5f` |
| 2 | `SC4011E0-PSG.edf` | Same dataset, `sleep-cassette/SC4011E0-PSG.edf` (different subject) | 51,110,528 bytes | EDF | `9e4c34f3f631275990fd7a2d61a24e998c7b03eb9b37ebe5cd7c8323d8310a15` |
| 3 | `SC4021E0-PSG.edf` | Same dataset, `sleep-cassette/SC4021E0-PSG.edf` (different subject again) | 51,147,008 bytes | EDF | `810adaedf42621e389ca767274045fbafb123bbc5d99bc15ce2c5bacb52e378c` |

All three are whole-night polysomnography recordings from PhysioNet's
Sleep-EDF Database Expanded (ODC-BY 1.0 / PhysioNet Open Access license,
same license terms already accepted for the existing `SC4001E0-PSG.edf`
fixture). Sizes are close to but distinct from each other and from the
already-fixtured `SC4001E0-PSG.edf` (48,338,048 bytes) — genuinely different
subjects/recording sessions, not copies.

No participant reidentification was attempted; these are the dataset's own
public, deidentified research recordings, used exactly as PhysioNet
publishes them.

## Evidence these were previously unseen

- Grepped every `sha256:` value in `testdata/manifest.yaml` — none of the
  three hashes above appear.
- Grepped `tests/python/test_real_data.py` and every other test file under
  `tests/python/` for the filenames `SC4002E0`, `SC4011E0`, `SC4021E0` —
  zero matches (only `SC4001E0`/`SC4001EC` are referenced, from the existing
  fixture).
- These files were never added to git; `git log --all --oneline -- '*SC40*'`
  shows only the existing `SC4001E0`/`SC4001EC` fixture pair referenced in
  documentation, never as tracked binary content (large fixtures are never
  committed, per `.gitignore`'s `*.edf` rule and the existing
  fetch-on-demand pattern in `scripts/fetch_test_data.py`).

## How they were used

All three were uploaded through the real browser UI (`/upload`, EDF format)
against the live Docker Compose stack — see
[docs/ADVERSARIAL_BROWSER_VALIDATION.md](ADVERSARIAL_BROWSER_VALIDATION.md)
for the full results of that pass, including per-recording differences
(channel count, duration, sampling rate) as actually ingested.
