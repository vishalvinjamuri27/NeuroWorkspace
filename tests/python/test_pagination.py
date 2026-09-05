"""Unit tests for cursor pagination, plus an end-to-end check that the
paginated endpoints behave correctly across multiple pages and reject a
tampered cursor -- the property offset pagination cannot give you: a stable
page boundary anchored to a row, not a row count.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from neuro_workspace.config import Settings
from neuro_workspace.db.models import Base, Workspace
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.errors import ValidationError
from neuro_workspace.pagination import (
    clamp_limit,
    encode_datetime_cursor,
    encode_float_cursor,
    paginate,
)
from test_api_integration import create_workspace_project


def test_clamp_limit_bounds_to_max_and_at_least_one() -> None:
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(10_000) == 200
    assert clamp_limit(50) == 50


def test_decoding_a_tampered_cursor_raises_validation_error() -> None:
    from neuro_workspace.pagination import _decode

    with pytest.raises(ValidationError, match=r"invalid_cursor|invalid or expired"):
        _decode("not-a-real-cursor")


def test_encode_decode_roundtrip_preserves_ordering_semantics() -> None:
    from neuro_workspace.pagination import _decode

    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = encode_datetime_cursor(now, "row-1")
    value, row_id = _decode(token)
    assert value == now
    assert row_id == "row-1"

    token_f = encode_float_cursor(12.5, "row-2")
    value_f, row_id_f = _decode(token_f)
    assert value_f == 12.5
    assert row_id_f == "row-2"


@pytest.fixture
def factory(tmp_path):
    settings = Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'page.db'}")
    engine, factory = create_engine_and_factory(settings.database_url)
    Base.metadata.create_all(engine)
    return factory


def test_paginate_walks_every_row_exactly_once_across_pages(factory) -> None:
    session = factory()
    try:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for index in range(23):
            workspace = Workspace(
                name=f"Lab {index}",
                slug=f"lab-{index}",
                created_at=base + timedelta(seconds=index),
            )
            session.add(workspace)
        session.commit()

        seen: list[str] = []
        cursor = None
        for _ in range(10):  # generous upper bound on page count
            stmt = select(Workspace).order_by(Workspace.created_at.desc(), Workspace.id.desc())
            page, has_more = paginate(
                session,
                stmt,
                sort_column=Workspace.created_at,
                id_column=Workspace.id,
                cursor=cursor,
                limit=7,
            )
            seen.extend(row.id for row in page)
            if not has_more:
                break
            cursor = encode_datetime_cursor(page[-1].created_at, page[-1].id)
        else:
            pytest.fail("did not terminate within the expected number of pages")

        assert len(seen) == 23
        assert len(set(seen)) == 23  # no row repeated across pages
    finally:
        session.close()


def test_recordings_endpoint_paginates_and_rejects_bad_cursor(
    signed_in_client: TestClient,
) -> None:
    from test_api_integration import upload_csv

    client = signed_in_client
    _, project_id = create_workspace_project(client)
    ids = set()
    for _ in range(3):
        recording_id, _ = upload_csv(client, project_id)
        ids.add(recording_id)

    first = client.get(f"/api/projects/{project_id}/recordings", params={"limit": 2})
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert len(first_page["items"]) == 2
    assert first_page["next_cursor"] is not None

    second = client.get(
        f"/api/projects/{project_id}/recordings",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    second_page = second.json()
    assert len(second_page["items"]) == 1
    assert second_page["next_cursor"] is None

    seen_ids = {row["id"] for row in first_page["items"] + second_page["items"]}
    assert seen_ids == ids

    bad = client.get(
        f"/api/projects/{project_id}/recordings", params={"cursor": "not-a-valid-cursor"}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_cursor"
