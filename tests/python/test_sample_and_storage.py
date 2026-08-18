from __future__ import annotations

import io
from pathlib import Path

import pytest
from sqlalchemy import select

from neuro_workspace.db.models import Base, Recording
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.errors import NotFoundError, ValidationError
from neuro_workspace.sample import seed_sample
from neuro_workspace.signal_store import LocalZarrSignalStore
from neuro_workspace.storage import LocalObjectStore


def test_local_object_store_streams_hashes_lists_and_deletes(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    info = store.put_stream("uploads/a/file.bin", io.BytesIO(b"signal"), 100)
    assert info.size_bytes == 6
    assert len(info.sha256 or "") == 64
    assert store.read_bytes(info.key) == b"signal"
    assert [item.key for item in store.list_prefix("uploads/a")] == [info.key]
    destination = tmp_path / "copy.bin"
    store.copy_to_path(info.key, destination)
    assert destination.read_bytes() == b"signal"
    assert store.delete_prefix("uploads/a") == 1
    with pytest.raises(NotFoundError):
        store.read_bytes(info.key)
    with pytest.raises(ValidationError):
        store.path_for("../escape")


@pytest.mark.slow
def test_sample_is_deterministic_public_and_idempotent(tmp_path: Path) -> None:
    engine, factory = create_engine_and_factory(f"sqlite:///{tmp_path / 'sample.db'}")
    Base.metadata.create_all(engine)
    signal_store = LocalZarrSignalStore(tmp_path / "signals")
    first = seed_sample(factory, signal_store, "test")
    assert seed_sample(factory, signal_store, "test") == first
    with factory() as session:
        recording = session.scalar(select(Recording).where(Recording.id == first))
        assert recording is not None
        assert recording.is_demo
        assert recording.safe_metadata["contains_human_data"] is False
        assert recording.safe_metadata["duration_seconds"] == 300.0
        version_id = recording.active_version_id
    assert version_id is not None
    window = signal_store.query(version_id, [0, 15], 29.5, 1.0, 400)
    assert len(window.channels) == 2
    assert max(value for value in window.channels[0].maximum if value is not None) > 50e-6
    engine.dispose()
