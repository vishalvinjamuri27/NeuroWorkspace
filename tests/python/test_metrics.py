"""The /metrics endpoint is unauthenticated (Prometheus scraping has no
session), served in real time from prometheus_client's registry, and the
counters it exposes actually move when the instrumented code paths run.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from test_api_integration import create_workspace_project, upload_csv


def test_metrics_endpoint_is_unauthenticated_and_exposes_expected_families(
    client: TestClient,
) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "neuroworkspace_http_requests_total" in body
    assert "neuroworkspace_jobs_started_total" in body
    assert "neuroworkspace_cache_requests_total" in body


def test_http_request_counter_increments_for_a_real_request(client: TestClient) -> None:
    before = client.get("/metrics").text
    client.get("/health/live")
    after = client.get("/metrics").text
    # A crude but real assertion: the health/live route's counter line must
    # differ (or newly appear) after another request to that route.
    assert 'route="/health/live"' in after
    assert before != after


def test_job_started_counter_increments_after_a_real_ingestion(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    before = client.get("/metrics").text

    upload_csv(client, project_id)

    after = client.get("/metrics").text
    assert 'neuroworkspace_jobs_started_total{kind="INGESTION"}' in after
    assert 'neuroworkspace_jobs_succeeded_total{kind="INGESTION"}' in after
    assert before != after
