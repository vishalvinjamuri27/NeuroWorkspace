"""Starts an upload and completes it (dispatching the ingestion job) but
does not wait for the job to finish -- used by the worker-crash runtime
validation scenario to get a job in flight before killing the worker.
Prints "RECORDING_ID=... JOB_ID=..." on success.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

BASE = "http://localhost:8000/api"
EEG_FILE = Path(
    ".cache/neuro-workspace/testdata/openneuro-ds004362-bids"
    "/sub-001/eeg/sub-001_task-motion_run-1_eeg.set"
)


def main() -> None:
    content = EEG_FILE.read_bytes()
    client = httpx.Client(timeout=30)
    client.post(
        f"{BASE}/session/dev",
        json={"email": "crash-test@example.test", "display_name": "Crash Test"},
    )
    workspace = client.post(f"{BASE}/workspaces", json={"name": "Crash Test Lab"}).json()
    project = client.post(
        f"{BASE}/workspaces/{workspace['id']}/projects",
        json={"name": "Crash Project", "description": "worker-crash scenario"},
    ).json()
    initiation = client.post(
        f"{BASE}/projects/{project['id']}/uploads",
        json={
            "recording_name": "Worker crash test recording",
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
    ).json()
    asset = initiation["assets"][0]
    put_response = httpx.put(asset["upload_url"], content=content, timeout=60)
    if put_response.status_code not in (200, 204):
        print(f"upload PUT failed: {put_response.status_code}", file=sys.stderr)
        sys.exit(1)
    complete = client.post(f"{BASE}/uploads/{initiation['id']}/complete").json()
    print(f"RECORDING_ID={complete['recording_id']} JOB_ID={complete['job_id']}")


if __name__ == "__main__":
    main()
