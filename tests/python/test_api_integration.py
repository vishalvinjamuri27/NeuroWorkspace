from __future__ import annotations

import io
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from conftest import sign_in
from neuro_api.main import create_app
from neuro_workspace.auth import Identity, OIDCVerifier
from neuro_workspace.config import Settings


def create_workspace_project(client: TestClient) -> tuple[str, str]:
    workspace = client.post("/api/workspaces", json={"name": "EEG Lab"})
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["id"]
    project = client.post(
        f"/api/workspaces/{workspace_id}/projects",
        json={"name": "Pilot", "description": "Reproducible review"},
    )
    assert project.status_code == 201, project.text
    return workspace_id, project.json()["id"]


def upload_csv(client: TestClient, project_id: str) -> tuple[str, str]:
    content = (
        "time,C3,C4\n"
        + "\n".join(f"{index / 128:.8f},{index},{-index}" for index in range(256))
        + "\n"
    ).encode()
    initiation = client.post(
        f"/api/projects/{project_id}/uploads",
        json={
            "recording_name": "CSV integration fixture",
            "format_hint": "csv",
            "assets": [
                {
                    "name": "fixture.csv",
                    "size_bytes": len(content),
                    "role": "primary",
                    "media_type": "text/csv",
                }
            ],
            "csv_config": {
                "sampling_frequency_hz": 128,
                "time_column": "time",
                "channel_columns": ["C3", "C4"],
                "channel_names": ["C3", "C4"],
                "units": ["uV", "uV"],
                "channel_types": ["eeg", "eeg"],
                "delimiter": ",",
            },
        },
    )
    assert initiation.status_code == 201, initiation.text
    payload = initiation.json()
    asset = payload["assets"][0]
    stored = client.put(
        asset["upload_url"], files={"file": ("fixture.csv", io.BytesIO(content), "text/csv")}
    )
    assert stored.status_code == 204, stored.text
    complete = client.post(f"/api/uploads/{payload['id']}/complete")
    assert complete.status_code == 200, complete.text
    return complete.json()["recording_id"], complete.json()["job_id"]


@pytest.mark.integration
def test_csv_upload_view_annotate_process_export_and_delete(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    workspace_id, project_id = create_workspace_project(client)
    assert len(client.get("/api/workspaces").json()) == 1
    assert len(client.get(f"/api/workspaces/{workspace_id}/projects").json()) == 1

    recording_id, job_id = upload_csv(client, project_id)
    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "SUCCEEDED"
    recording = client.get(f"/api/recordings/{recording_id}")
    assert recording.json()["state"] == "READY"
    assert recording.json()["safe_metadata"]["channel_count"] == 2
    original_version_id = recording.json()["active_version_id"]
    metrics = client.get(f"/api/workspaces/{workspace_id}/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["recordings_ready"] == 1
    assert metrics.json()["viewer_opens"] == 1
    assert len(client.get(f"/api/projects/{project_id}/recordings").json()) == 1
    assert client.get(f"/api/recordings/{recording_id}/events").json() == []

    channels = client.get(f"/api/recordings/{recording_id}/channels").json()
    assert [channel["name"] for channel in channels] == ["C3", "C4"]
    waveform = client.get(
        f"/api/recordings/{recording_id}/waveform",
        params={
            "start_seconds": 0,
            "duration_seconds": 2,
            "channel_index": [0, 1],
            "pixel_width": 80,
        },
    )
    assert waveform.status_code == 200, waveform.text
    assert len(waveform.json()["channels"]) == 2
    assert waveform.json()["samples_per_bin"] >= 1

    annotation = client.post(
        f"/api/recordings/{recording_id}/annotations",
        json={
            "onset_seconds": 0.25,
            "duration_seconds": 0.2,
            "label": "Review",
            "description": "Artifact candidate",
        },
    )
    assert annotation.status_code == 201, annotation.text
    annotation_id = annotation.json()["id"]
    conflict = client.patch(
        f"/api/annotations/{annotation_id}",
        json={
            "revision": 99,
            "onset_seconds": 0.3,
            "duration_seconds": 0.1,
            "label": "Changed",
            "description": "",
        },
    )
    assert conflict.status_code == 409
    assert (
        client.get(f"/api/recordings/{recording_id}/annotations").json()[0]["id"] == annotation_id
    )

    bad = client.put(
        f"/api/recordings/{recording_id}/bad-channels/{channels[0]['id']}",
        json={"state": "confirmed_bad", "reason": "Manual review"},
    )
    assert bad.status_code == 200
    qc = client.get(f"/api/recordings/{recording_id}/qc")
    assert qc.status_code == 200
    assert "not medical" in qc.json()["disclaimer"]

    processing = client.post(
        f"/api/recordings/{recording_id}/processing-runs",
        json={"operation_id": "average_reference", "parameters": {"channel_indices": [0, 1]}},
    )
    assert processing.status_code == 202, processing.text
    runs = client.get(f"/api/recordings/{recording_id}/processing-runs").json()
    assert runs[0]["status"] == "SUCCEEDED"
    assert runs[0]["output_version_id"] is not None

    exported = client.post(
        f"/api/recordings/{recording_id}/exports", json={"format_id": "annotations_csv"}
    )
    assert exported.status_code == 201, exported.text
    download = client.get(f"/api/exports/{exported.json()['id']}/content")
    assert download.status_code == 200
    assert "Review" in download.text
    for format_id in ("provenance_json", "qc_json"):
        result = client.post(
            f"/api/recordings/{recording_id}/exports", json={"format_id": format_id}
        )
        assert result.status_code == 201, result.text
        content = client.get(f"/api/exports/{result.json()['id']}/content")
        assert content.status_code == 200
        assert content.headers["content-type"].startswith("application/json")

    updated = client.patch(
        f"/api/annotations/{annotation_id}",
        json={
            "revision": 1,
            "onset_seconds": 0.3,
            "duration_seconds": 0.1,
            "label": "Reviewed",
            "description": "confirmed",
        },
    )
    assert updated.status_code == 200
    removed = client.request("DELETE", f"/api/annotations/{annotation_id}", json={"revision": 2})
    assert removed.status_code == 204
    assert client.get(f"/api/recordings/{recording_id}/annotations").json() == []

    sign_in(client, "outsider@example.test")
    # Resource existence is intentionally hidden across workspace boundaries.
    assert client.get(f"/api/recordings/{recording_id}").status_code == 404
    assert client.get(f"/api/exports/{exported.json()['id']}/content").status_code == 404
    sign_in(client)
    deleted = client.delete(f"/api/recordings/{recording_id}")
    assert deleted.status_code == 202, deleted.text
    assert client.get(f"/api/recordings/{recording_id}").status_code == 404
    assert not client.app.state.runtime.signal_store.version_path(original_version_id).exists()


def test_request_boundary_and_validation(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "live"}
    denied = client.post(
        "/api/session/dev",
        headers={"Origin": "https://attacker.example"},
        json={"email": "person@example.test", "display_name": "Person"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "origin_forbidden"
    invalid = client.post("/api/session/dev", json={"email": "x", "display_name": ""})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"


def test_public_alpha_upload_boundaries_reject_unsafe_inputs(
    signed_in_client: TestClient, settings: Settings
) -> None:
    _, project_id = create_workspace_project(signed_in_client)
    base = {
        "recording_name": "Boundary fixture",
        "format_hint": "edf",
        "assets": [
            {
                "name": "recording.edf",
                "size_bytes": 1,
                "role": "primary",
                "media_type": "application/octet-stream",
            }
        ],
    }
    unsafe_name = signed_in_client.post(
        f"/api/projects/{project_id}/uploads",
        json={**base, "assets": [{**base["assets"][0], "name": "../patient.edf"}]},
    )
    assert unsafe_name.status_code == 422
    assert unsafe_name.json()["error"]["code"] == "unsafe_filename"

    oversized = signed_in_client.post(
        f"/api/projects/{project_id}/uploads",
        json={
            **base,
            "assets": [
                {
                    **base["assets"][0],
                    "size_bytes": settings.max_upload_bytes + 1,
                }
            ],
        },
    )
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "too_large"

    unsupported = signed_in_client.post(
        f"/api/projects/{project_id}/uploads",
        json={**base, "format_hint": "zip"},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "invalid_request"


def test_oidc_authorization_code_pkce_and_state_validation(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    oidc_settings = settings.model_copy(
        update={
            "dev_auth_enabled": False,
            "oidc_issuer": "http://identity.example.test",
            "oidc_audience": "neuroworkspace-alpha",
            "oidc_client_id": "alpha-client",
            "oidc_client_secret": SecretStr("test-provider-secret"),
            "web_public_url": "http://web.example.test",
        }
    )

    class FakeResponse:
        def __init__(self, payload: dict[str, str]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return self._payload

    discovery = {
        "issuer": "http://identity.example.test",
        "authorization_endpoint": "http://identity.example.test/authorize",
        "token_endpoint": "http://identity.example.test/token",
    }
    monkeypatch.setattr(
        "neuro_api.routes_session.httpx.get", lambda *args, **kwargs: FakeResponse(discovery)
    )
    token_requests: list[dict[str, str]] = []

    def fake_token_request(*args: object, **kwargs: object) -> FakeResponse:
        token_requests.append(dict(kwargs["data"]))  # type: ignore[arg-type]
        return FakeResponse({"id_token": "verified-by-test-double"})

    monkeypatch.setattr("neuro_api.routes_session.httpx.post", fake_token_request)

    def fake_verify(self: OIDCVerifier, token: str, *, nonce: str | None = None) -> Identity:
        assert token == "verified-by-test-double"
        assert nonce and len(nonce) >= 32
        return Identity("oidc:alpha-user", "alpha@example.test", "Alpha User")

    monkeypatch.setattr(OIDCVerifier, "verify", fake_verify)

    with TestClient(create_app(oidc_settings)) as oidc_client:
        begin = oidc_client.get(
            "/api/session/oidc/login",
            params={"return_to": "/workspace"},
            follow_redirects=False,
        )
        assert begin.status_code == 302
        authorization = urlparse(begin.headers["location"])
        query = parse_qs(authorization.query)
        assert authorization.path == "/authorize"
        assert query["code_challenge_method"] == ["S256"]
        assert query["nonce"][0]
        assert query["state"][0]

        rejected = oidc_client.get(
            "/api/session/oidc/callback",
            params={"code": "alpha-code", "state": "tampered"},
            follow_redirects=False,
        )
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] == "invalid_login_state"

        complete = oidc_client.get(
            "/api/session/oidc/callback",
            params={"code": "alpha-code", "state": query["state"][0]},
            follow_redirects=False,
        )
        assert complete.status_code == 302
        assert complete.headers["location"] == "http://web.example.test/workspace"
        assert token_requests[0]["code_verifier"]
        assert token_requests[0]["client_secret"] == "test-provider-secret"
        assert oidc_client.get("/api/session").json()["user"]["email"] == "alpha@example.test"


@pytest.mark.integration
def test_product_evidence_feedback_and_return_metrics_are_privacy_bounded(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    workspace_id, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)

    forbidden_properties = client.post(
        "/api/product-events",
        json={
            "name": "recording_opened",
            "session_id": "session-one",
            "recording_id": recording_id,
            "properties": {"filename": "patient-name.edf"},
        },
    )
    assert forbidden_properties.status_code == 422

    for session_id in ("session-one", "session-two"):
        opened = client.post(
            "/api/product-events",
            json={
                "name": "recording_opened",
                "session_id": session_id,
                "recording_id": recording_id,
            },
        )
        assert opened.status_code == 204, opened.text
    assert (
        client.post(
            "/api/product-events",
            json={
                "name": "viewer_zoomed",
                "session_id": "session-two",
                "recording_id": recording_id,
            },
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/product-events",
            json={
                "name": "qc_opened",
                "session_id": "session-two",
                "recording_id": recording_id,
            },
        ).status_code
        == 204
    )
    channels = client.get(f"/api/recordings/{recording_id}/channels").json()
    annotation = client.post(
        f"/api/recordings/{recording_id}/annotations",
        json={
            "onset_seconds": 0.1,
            "duration_seconds": 0.1,
            "label": "Private user-authored label",
            "description": "This content must not enter product events",
        },
    )
    assert annotation.status_code == 201
    assert (
        client.put(
            f"/api/recordings/{recording_id}/bad-channels/{channels[0]['id']}",
            json={"state": "confirmed_bad", "reason": "Private review reason"},
        ).status_code
        == 200
    )

    feedback = client.post(
        "/api/feedback",
        json={
            "category": "confusing_workflow",
            "message": "I could not find the return path.",
            "route": f"/recordings/{recording_id}",
            "workspace_id": workspace_id,
            "recording_id": recording_id,
            "may_contact": False,
            "contact_email": "must-not-be-retained@example.test",
        },
    )
    assert feedback.status_code == 201, feedback.text
    admin_feedback = client.get("/api/admin/feedback")
    assert admin_feedback.status_code == 200
    assert admin_feedback.json()[0]["contact_email"] is None

    metrics = client.get("/api/admin/product-metrics")
    assert metrics.status_code == 200, metrics.text
    payload = metrics.json()
    assert payload["event_counts"]["recording_reopened"] == 1
    assert payload["event_counts"]["annotation_created"] == 1
    assert payload["event_counts"]["bad_channel_marked"] == 1
    assert payload["format_counts"]["csv"] >= 1
    assert payload["size_bucket_counts"]["<100 MB"] >= 1
    assert payload["return_behavior_rate"] == 1.0

    runtime = client.app.state.runtime
    with runtime.session_factory() as session:
        from neuro_workspace.db.models import ProductEvent

        rows = session.scalars(select(ProductEvent)).all()
        serialized = repr([row.safe_properties for row in rows])
        assert "patient-name" not in serialized
        assert "Private user-authored" not in serialized
        assert "Private review reason" not in serialized


@pytest.mark.integration
def test_workspace_memberships_are_role_scoped_and_keep_an_owner(client: TestClient) -> None:
    sign_in(client)
    workspace_id, _ = create_workspace_project(client)
    owner = client.get(f"/api/workspaces/{workspace_id}/memberships").json()[0]

    sign_in(client, "collaborator@example.test")
    sign_in(client)
    added = client.put(
        f"/api/workspaces/{workspace_id}/memberships",
        json={"email": "collaborator@example.test", "role": "viewer"},
    )
    assert added.status_code == 200, added.text

    sign_in(client, "collaborator@example.test")
    assert client.get(f"/api/workspaces/{workspace_id}/projects").status_code == 200
    forbidden = client.post(
        f"/api/workspaces/{workspace_id}/projects",
        json={"name": "Unauthorized", "description": ""},
    )
    assert forbidden.status_code == 404

    sign_in(client)
    changed = client.patch(
        f"/api/workspaces/{workspace_id}/memberships/{added.json()['id']}",
        json={"role": "member"},
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "member"

    sign_in(client, "collaborator@example.test")
    assert (
        client.post(
            f"/api/workspaces/{workspace_id}/projects",
            json={"name": "Allowed for members", "description": ""},
        ).status_code
        == 201
    )
    assert (
        client.delete(f"/api/workspaces/{workspace_id}/memberships/{owner['id']}").status_code
        == 404
    )

    sign_in(client)
    last_owner = client.delete(f"/api/workspaces/{workspace_id}/memberships/{owner['id']}")
    assert last_owner.status_code == 409
    removed = client.delete(f"/api/workspaces/{workspace_id}/memberships/{added.json()['id']}")
    assert removed.status_code == 204
    metrics = client.get(f"/api/workspaces/{workspace_id}/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["membership_changes"] == 3
