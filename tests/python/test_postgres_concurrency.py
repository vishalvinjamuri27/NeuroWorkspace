"""Upload completion contended across real PostgreSQL connections.

test_upload_completion.py proves the transition is a conditional UPDATE, but
under SQLite the two claims run one after the other. Here two threads, each
on its own connection, race the same row: the second UPDATE must block on
the first's row lock and then match zero rows once the first commits.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from neuro_api.routes_uploads import _claim_upload_for_completion
from neuro_workspace.db.models import Project, Upload, User, Workspace
from neuro_workspace.db.session import create_engine_and_factory

pytestmark = pytest.mark.postgres


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("requires a migrated Postgres DATABASE_URL")
    engine, factory = create_engine_and_factory(database_url)
    yield factory
    engine.dispose()


def _open_upload(factory: sessionmaker[Session]) -> str:
    tag = uuid.uuid4().hex
    with factory() as session:
        user = User(
            external_subject=f"pg-race-{tag}",
            email=f"pg-race-{tag}@example.test",
            display_name="Race",
        )
        workspace = Workspace(name="Race Lab", slug=f"race-{tag}")
        session.add_all([user, workspace])
        session.flush()
        project = Project(workspace_id=workspace.id, name="Race")
        session.add(project)
        session.flush()
        upload = Upload(
            workspace_id=workspace.id,
            project_id=project.id,
            creator_id=user.id,
            status="UPLOADING",
        )
        session.add(upload)
        session.commit()
        return upload.id


def test_second_connection_blocks_on_the_row_lock_then_loses(
    factory: sessionmaker[Session],
) -> None:
    upload_id = _open_upload(factory)
    first = factory()
    try:
        assert _claim_upload_for_completion(first, upload_id) is True  # holds the row lock

        outcome: dict[str, bool] = {}

        def second_claim() -> None:
            with factory() as second:
                outcome["won"] = _claim_upload_for_completion(second, upload_id)
                second.commit()

        thread = threading.Thread(target=second_claim)
        thread.start()
        thread.join(timeout=1.0)
        assert thread.is_alive(), "second UPDATE should be waiting on the first's lock"

        first.commit()
        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert outcome == {"won": False}
    finally:
        first.close()


def test_many_simultaneous_claims_produce_exactly_one_winner(
    factory: sessionmaker[Session],
) -> None:
    upload_id = _open_upload(factory)
    contenders = 8
    start = threading.Barrier(contenders)
    results: list[bool] = []
    lock = threading.Lock()

    def claim() -> None:
        with factory() as session:
            start.wait()
            won = _claim_upload_for_completion(session, upload_id)
            session.commit()
        with lock:
            results.append(won)

    threads = [threading.Thread(target=claim) for _ in range(contenders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30.0)

    assert len(results) == contenders
    assert results.count(True) == 1
