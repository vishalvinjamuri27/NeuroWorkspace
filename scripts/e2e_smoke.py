"""Runtime-validation E2E script: drives the real HTTP API of a running
NeuroWorkspace stack exactly as a client would -- dev sign-in, presigned
direct-to-MinIO upload, polling job status, annotations, processing,
export, and reopening. Not a unit test double: every step is a real HTTP
request against a live docker-compose stack.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

# Overridable so this same script can drive either the Docker Compose stack
# (default, ports 8000/3000) or a port-forwarded Kubernetes Service
# without duplicating all 24 steps.
API_BASE_URL = os.environ.get("E2E_API_BASE", "http://localhost:8000")
WEB_BASE_URL = os.environ.get("E2E_WEB_BASE", "http://localhost:3000")
BASE = f"{API_BASE_URL}/api"
EEG_FILE = Path(
    ".cache/neuro-workspace/testdata/openneuro-ds004362-bids/sub-001/eeg/sub-001_task-motion_run-1_eeg.set"
)


def step(name: str) -> None:
    print(f"\n=== {name} ===", flush=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}")
        sys.exit(1)
    print(f"OK: {message}")


def poll_job(client: httpx.Client, job_id: str, *, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"{BASE}/jobs/{job_id}")
        check(response.status_code == 200, f"job {job_id} status endpoint reachable")
        job = response.json()
        if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return job
        time.sleep(1)
    print(f"FAIL: job {job_id} did not reach a terminal state within {timeout}s")
    sys.exit(1)


def main() -> None:
    check(EEG_FILE.exists(), f"real EEG fixture present at {EEG_FILE}")
    content = EEG_FILE.read_bytes()
    print(f"fixture size: {len(content)} bytes")

    client = httpx.Client(timeout=30)

    step("1. Application loads")
    response = client.get(f"{WEB_BASE_URL}/")
    check(response.status_code == 200, "web app root returns 200")

    step("2. Dev auth sign-in")
    response = client.post(
        f"{BASE}/session/dev",
        json={"email": "e2e-smoke@example.test", "display_name": "E2E Smoke"},
    )
    check(response.status_code == 200, f"dev sign-in succeeds (got {response.status_code})")
    session_response = client.get(f"{BASE}/session")
    check(session_response.status_code == 200, "session persists via cookie")

    step("3. Workspace/project access")
    workspace = client.post(f"{BASE}/workspaces", json={"name": "E2E Smoke Lab"})
    check(workspace.status_code == 201, f"workspace created (got {workspace.status_code})")
    workspace_id = workspace.json()["id"]
    project = client.post(
        f"{BASE}/workspaces/{workspace_id}/projects",
        json={"name": "Smoke Project", "description": "Runtime validation"},
    )
    check(project.status_code == 201, f"project created (got {project.status_code})")
    project_id = project.json()["id"]

    step("4-6. Upload initiation, direct-to-object-store write, finalization")
    initiation = client.post(
        f"{BASE}/projects/{project_id}/uploads",
        json={
            "recording_name": "E2E smoke recording",
            "format_hint": "eeglab",
            "assets": [
                {
                    "name": "sub-001_task-motion_run-1_eeg.set",
                    "size_bytes": len(content),
                    "role": "primary",
                    "media_type": "application/octet-stream",
                }
            ],
        },
    )
    check(
        initiation.status_code == 201,
        f"upload initiated (got {initiation.status_code}, {initiation.text[:300]})",
    )
    payload = initiation.json()
    asset = payload["assets"][0]
    check(
        asset["direct"] is True,
        "upload URL is a direct presigned object-store URL, not proxied through the API",
    )
    print(f"presigned URL host: {httpx.URL(asset['upload_url']).host}")

    put_response = httpx.put(asset["upload_url"], content=content, timeout=60)
    check(
        put_response.status_code in (200, 204),
        f"direct PUT to object storage succeeded (got {put_response.status_code})",
    )

    complete = client.post(f"{BASE}/uploads/{payload['id']}/complete")
    check(
        complete.status_code == 200,
        f"upload finalized (got {complete.status_code}, {complete.text[:300]})",
    )
    complete_body = complete.json()
    recording_id = complete_body["recording_id"]
    job_id = complete_body["job_id"]
    print(f"recording_id={recording_id} job_id={job_id}")

    step("7-9. Ingestion job durable, worker claims it, recording transitions state")
    job = poll_job(client, job_id, timeout=180)
    check(
        job["status"] == "SUCCEEDED",
        f"ingestion job SUCCEEDED (got {job['status']}: {job.get('error_message')})",
    )

    step("10-12. Canonical signal data + pyramids persisted, recording READY")
    recording = client.get(f"{BASE}/recordings/{recording_id}")
    check(recording.status_code == 200, "recording metadata endpoint works (step 13)")
    recording_body = recording.json()
    check(
        recording_body["state"] == "READY",
        f"recording reached READY (got {recording_body['state']})",
    )
    check(recording_body["active_version_id"] is not None, "active_version_id set")
    channel_count = recording_body["safe_metadata"].get("channel_count")
    duration_seconds = recording_body["safe_metadata"].get("duration_seconds")
    print(f"channel_count={channel_count} duration_seconds={duration_seconds}")

    step("14. Viewport waveform endpoint returns bounded signal data")
    waveform = client.get(
        f"{BASE}/recordings/{recording_id}/waveform",
        params={"start_seconds": 0, "duration_seconds": 5, "pixel_width": 200},
    )
    check(waveform.status_code == 200, f"waveform endpoint works (got {waveform.status_code})")
    waveform_body = waveform.json()
    check(
        len(waveform_body["channels"]) > 0,
        f"waveform returned {len(waveform_body['channels'])} channels",
    )
    check("etag" in {k.lower() for k in waveform.headers}, "waveform response carries an ETag")

    step("16-17. Annotation create + revise")
    annotation = client.post(
        f"{BASE}/recordings/{recording_id}/annotations",
        json={
            "onset_seconds": 0.5,
            "duration_seconds": 0.2,
            "label": "Smoke test annotation",
            "description": "",
        },
    )
    check(
        annotation.status_code == 201,
        f"annotation created (got {annotation.status_code}, {annotation.text[:300]})",
    )
    annotation_body = annotation.json()
    annotation_id = annotation_body["id"]
    revision = annotation_body["revision"]

    updated = client.patch(
        f"{BASE}/annotations/{annotation_id}",
        json={
            "revision": revision,
            "onset_seconds": 0.6,
            "duration_seconds": 0.3,
            "label": "Smoke test annotation (revised)",
            "description": "revised via e2e_smoke.py",
        },
    )
    check(
        updated.status_code == 200,
        f"annotation revised (got {updated.status_code}, {updated.text[:300]})",
    )
    check(updated.json()["revision"] == revision + 1, "revision incremented on update")

    step("18. QC information")
    qc = client.get(f"{BASE}/recordings/{recording_id}/qc")
    check(qc.status_code == 200, f"QC endpoint works (got {qc.status_code})")
    qc_body = qc.json()
    print(f"qc metrics: {len(qc_body['metrics'])}, flags: {len(qc_body['flags'])}")

    step("19-20. Processing operation creates derived RecordingVersion; lineage")
    indices = list(range(min(4, channel_count or 4)))
    processing = client.post(
        f"{BASE}/recordings/{recording_id}/processing-runs",
        json={"operation_id": "average_reference", "parameters": {"channel_indices": indices}},
        headers={"Idempotency-Key": "e2e-smoke-average-reference"},
    )
    check(
        processing.status_code == 202,
        f"processing run started (got {processing.status_code}, {processing.text[:300]})",
    )
    processing_body = processing.json()
    processing_job_id = processing_body["job_id"]
    input_version_id = processing_body["input_version_id"]

    processing_job = poll_job(client, processing_job_id, timeout=60)
    check(
        processing_job["status"] == "SUCCEEDED",
        f"processing job SUCCEEDED (got {processing_job['status']})",
    )

    runs = client.get(f"{BASE}/recordings/{recording_id}/processing-runs")
    check(runs.status_code == 200, "processing runs listable")
    run_row = next(r for r in runs.json() if r["job_id"] == processing_job_id)
    check(run_row["status"] == "SUCCEEDED", "processing run row reflects SUCCEEDED")
    check(run_row["output_version_id"] is not None, "derived RecordingVersion id present")
    check(
        run_row["input_version_id"] == input_version_id,
        "lineage: input_version_id matches the version processed",
    )
    print(f"derived version_id={run_row['output_version_id']} parent={run_row['input_version_id']}")

    step("21-23. Export request, completion, download")
    export = client.post(
        f"{BASE}/recordings/{recording_id}/exports", json={"format_id": "annotations_csv"}
    )
    check(
        export.status_code == 201, f"export created (got {export.status_code}, {export.text[:300]})"
    )
    export_body = export.json()
    check(
        export_body["status"] == "READY",
        f"export completed synchronously as READY (got {export_body['status']})",
    )
    download = client.get(f"{BASE}/exports/{export_body['id']}/content")
    check(download.status_code == 200, f"export downloadable (got {download.status_code})")
    check(
        "Smoke test annotation" in download.text, "exported CSV contains the annotation we created"
    )

    step("24. Reopening the recording retains expected state")
    reopened = client.get(f"{BASE}/recordings/{recording_id}")
    check(reopened.status_code == 200, "recording reachable again")
    check(reopened.json()["state"] == "READY", "state persisted across requests")
    annotations = client.get(f"{BASE}/recordings/{recording_id}/annotations")
    check(annotations.status_code == 200, "annotations listable on reopen")
    items = annotations.json()["items"]
    check(
        any(a["id"] == annotation_id and a["revision"] == revision + 1 for a in items),
        "revised annotation persisted with correct revision",
    )

    print("\n=== ALL STEPS PASSED ===")


if __name__ == "__main__":
    main()
