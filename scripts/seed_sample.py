"""Seed the deterministic public sample into the configured environment."""

from __future__ import annotations

from neuro_workspace.config import get_settings
from neuro_workspace.db.models import Base
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.sample import seed_sample
from neuro_workspace.signal_store import create_signal_store


def main() -> None:
    settings = get_settings()
    engine, factory = create_engine_and_factory(settings.database_url)
    Base.metadata.create_all(engine)
    signal_store = create_signal_store(settings)
    recording_id = seed_sample(factory, signal_store, settings.build_version)
    print(f"Sample recording ready: {recording_id}")
    engine.dispose()


if __name__ == "__main__":
    main()
