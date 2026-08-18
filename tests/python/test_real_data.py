from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from neuro_workspace.adapters import BDFAdapter, BrainVisionAdapter, EEGLABAdapter, FIFAdapter
from neuro_workspace.adapters.mne_adapter import EDFAdapter
from neuro_workspace.ingestion import ingest_recording
from neuro_workspace.signal_store import LocalZarrSignalStore


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


@pytest.mark.real_data
@pytest.mark.filterwarnings("error:Loading an EDF with mixed sampling frequencies.*")
def test_physionet_sleep_edf_metadata_windows_and_annotations(
    real_data_root: Path, tmp_path: Path
) -> None:
    signal = real_data_root / "sleep-cassette" / "SC4001E0-PSG.edf"
    annotations = real_data_root / "sleep-cassette" / "SC4001EC-Hypnogram.edf"
    assert digest(signal) == "2b40a18adf76af69a42d6db1f30f31d26b369f6d27ca0050ef30147ef892b131"
    assert digest(annotations) == "a4cf67694ade1b52a0ddd06d5817fd45d2d3e8bac5302f640f3e9cfbbf12a996"
    adapter = EDFAdapter()
    metadata = adapter.inspect_metadata([signal, annotations])
    assert len(metadata.channels) == 7
    assert metadata.duration_seconds == pytest.approx(79_500, abs=0.01)
    assert {channel.source_sampling_frequency_hz for channel in metadata.channels} == {
        1.0,
        100.0,
    }
    assert len(metadata.events) >= 1

    store = LocalZarrSignalStore(tmp_path / "canonical")
    result = ingest_recording(
        adapter=adapter,
        assets=[signal, annotations],
        import_config=None,
        version_id="sleep-edf-real",
        signal_store=store,
        source_hashes=(digest(signal), digest(annotations)),
        build_version="real-data-test",
        qc_window_seconds=30,
    )
    assert result.manifest.sample_count == 7_950_000
    raw = store.read_raw_window("sleep-edf-real", 0, 1000)
    assert raw.shape == (7, 1000)
    assert np.isfinite(raw).all()
    envelope = store.query("sleep-edf-real", [0, 1, 6], 0, 3600, 1200)
    assert len(envelope.bin_start_seconds) <= 1800
    assert envelope.samples_per_bin > 1
    assert all(channel.valid_count for channel in envelope.channels)


@pytest.mark.real_data
@pytest.mark.parametrize(
    ("adapter", "relative_paths", "hashes", "channels", "sfreq", "samples", "events"),
    [
        (
            BDFAdapter(),
            ["hbn-bdf/sub-NDARAC904DMU_task-DiaryOfAWimpyKid_eeg.bdf"],
            ["dd714436de85002cc7284fd624f8f7bb8e6260bb55718c88d8f313cd5e5d367a"],
            129,
            100.0,
            12_100,
            0,
        ),
        (
            EEGLABAdapter(),
            ["openneuro-ds004362/sub-001_task-motion_run-1_eeg.set"],
            ["affd2c020dab82aa2bd38363fc6f813fbb7239c54bccd7b70c35a23bd75b71b7"],
            64,
            160.0,
            9_760,
            1,
        ),
        (
            BrainVisionAdapter(),
            [
                "openneuro-ds006547/sub-01_ses-01_task-visual_eeg.vhdr",
                "openneuro-ds006547/sub-01_ses-01_task-visual_eeg.vmrk",
                "openneuro-ds006547/sub-01_ses-01_task-visual_eeg.eeg",
            ],
            [
                "5a60f91a267ed04a45df9e0dd71d83e702977bde2a232f9d7be117654551affb",
                "f036b8804afe029b6ef62771f000c7e05ffa2b58fafef5ecacadfa33d4e1cf45",
                "b4d67fe4541f664afe4bb39d48740823132af600fdda5957007b4dd9ba6363be",
            ],
            67,
            500.0,
            1_410_630,
            456,
        ),
        (
            FIFAdapter(),
            ["mne-testing-data/sample_audvis_trunc_raw.fif"],
            ["d1ecdebf1ae23d9e7a5b56be63f45191778440cce6c8dfb0c270e75484b2aa50"],
            376,
            300.3074951171875,
            6_007,
            0,
        ),
    ],
    ids=["bdf", "eeglab", "brainvision", "fif"],
)
def test_public_format_metadata_and_lazy_window(
    real_data_root: Path,
    adapter: BDFAdapter | EEGLABAdapter | BrainVisionAdapter | FIFAdapter,
    relative_paths: list[str],
    hashes: list[str],
    channels: int,
    sfreq: float,
    samples: int,
    events: int,
) -> None:
    paths = [real_data_root / relative_path for relative_path in relative_paths]
    assert [digest(path) for path in paths] == hashes
    adapter.validate(paths)
    metadata = adapter.inspect_metadata(paths)
    assert len(metadata.channels) == channels
    assert metadata.sampling_frequency_hz == pytest.approx(sfreq)
    assert metadata.sample_count == samples
    assert len(metadata.events) == events
    first = next(adapter.iter_signal_chunks(paths, min(samples, 4096)))
    assert first.values.shape == (channels, min(samples, 4096))
    assert np.isfinite(first.values).all()
