"""Proves completing an upload is a single atomic transition, so two racing
completion requests cannot each create a Recording and an ingestion Job.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from neuro_api import routes_uploads
from neuro_workspace.db.models import Recording, Upload
from test_api_integration import create_workspace_project


def _open_csv_upload(client: TestClient, project_id: str) -> str:
    content = b"time,C3\n0,1\n0.0078125,2\n"
    initiation = client.post(
        f"/api/projects/{project_id}/uploads",
        json={
            "recording_name": "Completion race fixture",
            "format_hint": "csv",
            "assets": [
                {
                    "name": "race.csv",
                    "size_bytes": len(content),
                    "role": "primary",
                    "media_type": "text/csv",
                }
            ],
            "csv_config": {
                "sampling_frequency_hz": 128,
                "time_column": "time",
                "channel_columns": ["C3"],
                "channel_names": ["C3"],
                "units": ["uV"],
                "channel_types": ["eeg"],
                "delimiter": ",",
            },
        },
    )
    assert initiation.status_code == 201, initiation.text
    payload = initiation.json()
    stored = client.put(
        payload["assets"][0]["upload_url"],
        files={"file": ("race.csv", io.BytesIO(content), "text/csv")},
    )
    assert stored.status_code == 204, stored.text
    return str(payload["id"])


def test_two_sessions_racing_to_complete_the_same_upload_do_not_both_win(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    upload_id = _open_csv_upload(client, project_id)

    factory = client.app.state.runtime.session_factory
    session_a = factory()
    session_b = factory()
    try:
        assert session_a.get(Upload, upload_id).status == "UPLOADING"
        assert session_b.get(Upload, upload_id).status == "UPLOADING"

        won_a = routes_uploads._claim_upload_for_completion(session_a, upload_id)
        session_a.commit()
        won_b = routes_uploads._claim_upload_for_completion(session_b, upload_id)
        session_b.commit()

        assert (won_a, won_b) == (True, False)
    finally:
        session_a.close()
        session_b.close()


def test_completion_that_loses_the_race_returns_conflict_and_creates_nothing(
    signed_in_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drives the real endpoint: a competing request completes the upload after
    this request has already read it as UPLOADING but before it claims it --
    the interleaving the old read-then-write code let through as a duplicate
    Recording.
    """

    client = signed_in_client
    _, project_id = create_workspace_project(client)
    upload_id = _open_csv_upload(client, project_id)
    factory = client.app.state.runtime.session_factory
    real_claim = routes_uploads._claim_upload_for_completion

    def claim_after_competitor_wins(session, claimed_upload_id: str) -> bool:  # type: ignore[no-untyped-def]
        with factory() as competitor:
            competitor.execute(
                update(Upload).where(Upload.id == claimed_upload_id).values(status="COMPLETED")
            )
            competitor.commit()
        return real_claim(session, claimed_upload_id)

    monkeypatch.setattr(routes_uploads, "_claim_upload_for_completion", claim_after_competitor_wins)

    response = client.post(f"/api/uploads/{upload_id}/complete")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "upload_not_open"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Recording)) == 0


def test_completing_an_upload_twice_is_refused(signed_in_client: TestClient) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    upload_id = _open_csv_upload(client, project_id)

    first = client.post(f"/api/uploads/{upload_id}/complete")
    second = client.post(f"/api/uploads/{upload_id}/complete")

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    factory = client.app.state.runtime.session_factory
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Recording)) == 1
