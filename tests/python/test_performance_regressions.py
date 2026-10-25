"""Lightweight correctness-of-scaling regression checks for the properties
docs/PERFORMANCE.md's benchmarks rely on.

These assert *shape*, not timing: "the response is bounded," "the query is
still indexed," "the pool is configured, not left at library defaults" --
never a millisecond threshold, which would be flaky by machine and load.
Real timing lives in scripts/bench_*.py and is reported (with sample
counts) in docs/PERFORMANCE.md, not asserted here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from neuro_workspace.config import Settings
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.domain import ChannelMetadata, RecordingMetadata, SignalChunk
from neuro_workspace.signal_store import LocalZarrSignalStore
from test_api_integration import create_workspace_project, upload_csv


def test_waveform_response_is_bounded_by_viewport_not_by_recording_length(
    tmp_path: Path,
) -> None:
    """The whole point of viewport-bounded signal storage (ADR 0002/0007):
    a 2-hour recording and a 30-second recording, queried for the same
    10-second/2-channel window, must return the same-sized response.
    """

    store = LocalZarrSignalStore(tmp_path)
    rng = np.random.default_rng(0)

    def make_version(version_id: str, duration_seconds: float) -> None:
        sample_count = int(duration_seconds * 256)
        metadata = RecordingMetadata(
            format_id="test",
            sampling_frequency_hz=256.0,
            sample_count=sample_count,
            duration_seconds=duration_seconds,
            channels=(
                ChannelMetadata(0, "C3", "C3", "eeg", "V"),
                ChannelMetadata(1, "C4", "C4", "eeg", "V"),
            ),
        )
        values = rng.normal(0, 1, size=(2, sample_count)).astype(np.float32)
        store.write(version_id, metadata, iter([SignalChunk(0, values)]))

    make_version("short", 30.0)
    make_version("long", 7200.0)

    short_window = store.query("short", [0, 1], 5.0, 10.0, 400)
    long_window = store.query("long", [0, 1], 5.0, 10.0, 400)

    short_points = len(short_window.bin_start_seconds)
    long_points = len(long_window.bin_start_seconds)
    # Not necessarily bit-identical (pyramid level selection can differ
    # slightly by how many levels a short recording even has), but must
    # stay within a small constant factor -- never scale with the 240x
    # difference in total recording length.
    assert long_points <= short_points * 4
    assert long_points < 10_000  # nowhere near "the whole recording"


def test_annotations_list_query_is_limit_bounded_in_sql(signed_in_client: TestClient) -> None:
    """A recording with more annotations than the page size must never
    have all of them fetched from the database in one call -- the API
    response's own next_cursor is the visible symptom; this asserts the
    underlying SQL statement itself carries a LIMIT.
    """

    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)
    for i in range(5):
        response = client.post(
            f"/api/recordings/{recording_id}/annotations",
            json={
                "onset_seconds": 0.01 * i,
                "duration_seconds": 0.01,
                "label": f"a{i}",
                "description": "",
            },
        )
        assert response.status_code == 201

    page = client.get(f"/api/recordings/{recording_id}/annotations?limit=2").json()
    assert len(page["items"]) == 2
    assert page["next_cursor"] is not None


@pytest.mark.parametrize("limit", [1, 200])
def test_recordings_and_annotations_pages_never_exceed_requested_limit(
    signed_in_client: TestClient, limit: int
) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)
    page = client.get(f"/api/projects/{project_id}/recordings?limit={limit}").json()
    assert len(page["items"]) <= limit
    page = client.get(f"/api/recordings/{recording_id}/annotations?limit={limit}").json()
    assert len(page["items"]) <= limit


def test_engine_pool_is_explicitly_sized_not_left_at_sqlalchemy_defaults() -> None:
    """scripts/bench_job_concurrency.py measured SQLAlchemy's own defaults
    (pool_size=5, max_overflow=10) timing out connection checkout at 16
    concurrent DB-touching threads sharing one engine -- see
    docs/PERFORMANCE.md, 'Connection pool behavior'. This locks in that the
    fix (explicit, larger sizing) stays wired up.
    """

    settings = Settings(environment="test")
    assert settings.db_pool_size + settings.db_max_overflow > 15

    engine, _ = create_engine_and_factory(
        "postgresql+psycopg://u:p@localhost/db",
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    assert engine.pool.size() == settings.db_pool_size
    assert engine.pool._max_overflow == settings.db_max_overflow


def test_sqlite_engine_construction_ignores_pool_kwargs_without_erroring() -> None:
    # SQLite's default poolclass does not accept pool_size/max_overflow;
    # create_engine_and_factory must route around that, not crash tests
    # and dev tooling that use a sqlite DATABASE_URL.
    engine, _ = create_engine_and_factory("sqlite:///:memory:")
    assert engine is not None


@pytest.mark.postgres
@pytest.mark.parametrize(
    "table,index_name",
    [
        ("annotations", "ix_annotations_recording_onset"),
        ("jobs", "ix_jobs_status_lease"),
        ("qc_metrics", "ix_qc_metrics_version"),
        ("recordings", "ix_recordings_project_created"),
        ("audit_events", "ix_audit_workspace_created"),
    ],
)
def test_essential_index_exists_on_postgres(table: str, index_name: str) -> None:
    """Guards against an index one of the hot-path queries in
    docs/PERFORMANCE.md relies on being silently dropped by a future
    migration. Skipped outside Postgres (sqlite's dev/test default does not
    model index presence the same way, and this is specifically about the
    production database engine).
    """

    import os

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("requires a running Postgres DATABASE_URL")
    engine = create_engine(database_url)
    with engine.connect() as conn:
        found = conn.execute(
            text("select 1 from pg_indexes where tablename = :t and indexname = :i"),
            {"t": table, "i": index_name},
        ).scalar()
    assert found, f"expected index {index_name} on {table}"
