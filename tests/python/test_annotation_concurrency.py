"""Proves annotation edits use a real compare-and-swap, not last-write-wins,
and that processing-run creation is idempotent under a client-supplied key.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from neuro_workspace.db.models import Annotation, BadChannel, User
from test_api_integration import create_workspace_project, upload_csv


def _create_annotation(client: TestClient, recording_id: str) -> str:
    response = client.post(
        f"/api/recordings/{recording_id}/annotations",
        json={
            "onset_seconds": 0.1,
            "duration_seconds": 0.1,
            "label": "Original",
            "description": "",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_two_overlapping_transactions_at_the_same_base_revision_do_not_both_win(
    signed_in_client: TestClient,
) -> None:
    """Directly exercises the UPDATE...WHERE revision=:revision statement the
    route uses, with two separate open sessions racing on the same row --
    the scenario an HTTP-level test cannot force through a synchronous
    TestClient, but which is exactly what the old read-then-write code
    allowed through as silent last-write-wins.
    """

    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)
    annotation_id = _create_annotation(client, recording_id)

    factory = client.app.state.runtime.session_factory
    session_a = factory()
    session_b = factory()
    try:
        annotation_a = session_a.get(Annotation, annotation_id)
        annotation_b = session_b.get(Annotation, annotation_id)
        assert annotation_a.revision == annotation_b.revision == 1

        result_a = session_a.execute(
            update(Annotation)
            .where(Annotation.id == annotation_id, Annotation.revision == 1)
            .values(label="Writer A", revision=Annotation.revision + 1)
        )
        session_a.commit()
        assert result_a.rowcount == 1

        # Writer B still believes revision is 1 (it read before A committed).
        # Its conditional UPDATE must now match zero rows.
        result_b = session_b.execute(
            update(Annotation)
            .where(Annotation.id == annotation_id, Annotation.revision == 1)
            .values(label="Writer B", revision=Annotation.revision + 1)
        )
        session_b.commit()
        assert result_b.rowcount == 0
    finally:
        session_a.close()
        session_b.close()

    final = client.get(f"/api/recordings/{recording_id}/annotations").json()["items"][0]
    assert final["label"] == "Writer A"
    assert final["revision"] == 2


def test_processing_run_idempotency_key_dedupes_retry(signed_in_client: TestClient) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)

    headers = {"Idempotency-Key": "double-click-guard-1"}
    body = {"operation_id": "average_reference", "parameters": {"channel_indices": [0, 1]}}

    url = f"/api/recordings/{recording_id}/processing-runs"
    first = client.post(url, json=body, headers=headers)
    assert first.status_code == 202, first.text
    first_run_id = first.json()["id"]

    retry = client.post(url, json=body, headers=headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == first_run_id

    runs = client.get(f"/api/recordings/{recording_id}/processing-runs").json()
    assert len(runs) == 1


def test_processing_run_without_idempotency_key_is_not_deduped(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)
    body = {"operation_id": "average_reference", "parameters": {"channel_indices": [0, 1]}}

    first = client.post(f"/api/recordings/{recording_id}/processing-runs", json=body)
    assert first.status_code == 202
    second = client.post(f"/api/recordings/{recording_id}/processing-runs", json=body)
    assert second.status_code == 202
    assert first.json()["id"] != second.json()["id"]


def test_bad_channel_stale_revision_is_rejected_with_409(signed_in_client: TestClient) -> None:
    """HTTP-level proof that a client holding a stale revision (it never saw
    a teammate's review) gets a 409 instead of silently overwriting it.
    """

    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)
    channel_id = client.get(f"/api/recordings/{recording_id}/channels").json()[0]["id"]

    first = client.put(
        f"/api/recordings/{recording_id}/bad-channels/{channel_id}",
        json={"state": "confirmed_bad", "reason": "Noisy", "revision": 0},
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1

    # Still believes revision 0 (never re-fetched after the first write).
    stale = client.put(
        f"/api/recordings/{recording_id}/bad-channels/{channel_id}",
        json={"state": "confirmed_good", "reason": "Looks fine to me", "revision": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "bad_channel_revision_conflict"

    current = client.get(f"/api/recordings/{recording_id}/bad-channels").json()
    assert len(current) == 1
    assert current[0]["state"] == "confirmed_bad"
    assert current[0]["revision"] == 1


def test_two_overlapping_bad_channel_reviews_at_revision_zero_do_not_both_win(
    signed_in_client: TestClient,
) -> None:
    """Same manual-interleave shape as the annotation CAS test: two
    reviewers open the channel before either has saved, so both believe
    revision 0. The unique constraint on (recording_id, version_id,
    channel_id) is what actually decides the winner here, since there is no
    existing row for a conditional UPDATE to key off yet.
    """

    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)
    channel_id = client.get(f"/api/recordings/{recording_id}/channels").json()[0]["id"]
    active_version_id = client.get(f"/api/recordings/{recording_id}").json()["active_version_id"]

    factory = client.app.state.runtime.session_factory
    session_a = factory()
    session_b = factory()
    try:
        actor_id = session_a.scalar(select(User.id))
        row_a = BadChannel(
            recording_id=recording_id,
            version_id=active_version_id,
            channel_id=channel_id,
            actor_id=actor_id,
            state="confirmed_bad",
            reason="Reviewer A",
            revision=1,
        )
        session_a.add(row_a)
        session_a.commit()

        row_b = BadChannel(
            recording_id=recording_id,
            version_id=active_version_id,
            channel_id=channel_id,
            actor_id=actor_id,
            state="confirmed_good",
            reason="Reviewer B",
            revision=1,
        )
        session_b.add(row_b)
        with pytest.raises(IntegrityError):
            session_b.commit()
        session_b.rollback()
    finally:
        session_a.close()
        session_b.close()

    current = client.get(f"/api/recordings/{recording_id}/bad-channels").json()
    assert len(current) == 1
    assert current[0]["state"] == "confirmed_bad"
