"""Exercise the production-like Compose stack through its public interfaces.

This script is intentionally a release-validation client, not application code. It
expects the checked-in Compose stack to be running and records only opaque test IDs
and a development session cookie in a caller-provided state file.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import boto3
import httpx
import yaml
from botocore.client import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
API_ORIGIN = os.environ.get("NEUROWORKSPACE_API_ORIGIN", "http://localhost:8000").rstrip("/")
WEB_ORIGIN = os.environ.get("NEUROWORKSPACE_WEB_ORIGIN", "http://localhost:3000").rstrip("/")
S3_ORIGIN = os.environ.get("NEUROWORKSPACE_S3_ORIGIN", "http://localhost:9000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = 120.0
JOB_TIMEOUT_SECONDS = 360.0
TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
REAL_SAMPLE_ID = "openneuro_ds004362_eeglab"


def _env_file_values() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.is_file():
        path = ROOT / ".env.example"
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _s3_client() -> Any:
    values = _env_file_values()
    return boto3.client(
        "s3",
        endpoint_url=S3_ORIGIN,
        aws_access_key_id=values["S3_ACCESS_KEY"],
        aws_secret_access_key=values["S3_SECRET_KEY"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _bucket() -> str:
    return _env_file_values()["S3_BUCKET"]


def _client(*, cookie: str | None = None) -> httpx.Client:
    client = httpx.Client(
        base_url=API_ORIGIN,
        headers={"Origin": WEB_ORIGIN},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    if cookie is not None:
        client.cookies.set(_env_file_values()["SESSION_COOKIE_NAME"], cookie)
    return client


def _expect(response: httpx.Response, status: int, label: str) -> httpx.Response:
    if response.status_code != status:
        body = response.text[:1000]
        raise RuntimeError(f"{label}: expected HTTP {status}, got {response.status_code}: {body}")
    return response


def _json(response: httpx.Response, status: int, label: str) -> Any:
    return _expect(response, status, label).json()


def _login(client: httpx.Client, email: str, display_name: str) -> dict[str, Any]:
    payload = _json(
        client.post(
            "/api/session/dev",
            json={"email": email, "display_name": display_name},
        ),
        200,
        f"sign in {email}",
    )
    return dict(payload["user"])


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _read_state(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _save_state(path: Path, state: dict[str, Any]) -> None:
    _write_state(path, state)
    print(f"state updated: {path}")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sample_spec() -> dict[str, Any]:
    manifest = yaml.safe_load((ROOT / "testdata" / "manifest.yaml").read_text(encoding="utf-8"))
    sample = dict(manifest["samples"][REAL_SAMPLE_ID])
    return next(dict(item) for item in sample["files"] if str(item["path"]).endswith("_eeg.set"))


def _download_seeded_real_sample() -> tuple[str, bytes]:
    client = _s3_client()
    bucket = _bucket()
    spec = _sample_spec()
    expected_size = int(spec["size_bytes"])
    expected_hash = str(spec["sha256"])
    response = client.list_objects_v2(Bucket=bucket, Prefix="uploads/")
    candidates = [
        str(item["Key"])
        for item in response.get("Contents", [])
        if str(item["Key"]).endswith(".set") and int(item["Size"]) == expected_size
    ]
    for key in candidates:
        with tempfile.TemporaryDirectory(prefix="nw-compose-real-") as temp_name:
            path = Path(temp_name) / "public-sample.set"
            client.download_file(bucket, key, str(path))
            data = path.read_bytes()
        if _sha256_bytes(data) == expected_hash:
            return key, data
    raise RuntimeError("checksum-pinned OpenNeuro sample was not present in seeded object storage")


def _upload(
    client: httpx.Client,
    *,
    project_id: str,
    name: str,
    filename: str,
    data: bytes,
    format_hint: str,
    csv_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "recording_name": name,
        "format_hint": format_hint,
        "assets": [
            {
                "name": filename,
                "size_bytes": len(data),
                "role": "primary",
                "media_type": "application/octet-stream",
            }
        ],
    }
    if csv_config is not None:
        request["csv_config"] = csv_config
    upload = dict(
        _json(
            client.post(f"/api/projects/{project_id}/uploads", json=request),
            201,
            f"initiate {name}",
        )
    )
    asset = dict(upload["assets"][0])
    if not asset["direct"]:
        raise RuntimeError("Compose S3 upload did not return a presigned direct-upload URL")
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as uploader:
        _expect(
            uploader.put(
                str(asset["upload_url"]),
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            ),
            200,
            f"upload {name} to object storage",
        )
    completed = dict(
        _json(
            client.post(f"/api/uploads/{upload['id']}/complete"),
            200,
            f"complete {name}",
        )
    )
    completed.update(
        {
            "upload_id": upload["id"],
            "asset_id": asset["id"],
            "source_key": f"uploads/{upload['id']}/{asset['id']}/{asset['safe_name']}",
        }
    )
    return completed


def _wait_job(
    client: httpx.Client,
    job_id: str,
    *,
    expected: str,
    timeout_seconds: float = JOB_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = dict(_json(client.get(f"/api/jobs/{job_id}"), 200, f"poll job {job_id}"))
        if latest["status"] in TERMINAL_JOB_STATES:
            break
        time.sleep(0.5)
    if latest.get("status") != expected:
        raise RuntimeError(f"job {job_id} expected {expected}, got {latest}")
    return latest


def _head_object(key: str) -> dict[str, Any]:
    return dict(_s3_client().head_object(Bucket=_bucket(), Key=key))


def _canonical_manifest_key(version_id: str) -> str:
    # Mirrors S3ZarrSignalStore's default "canonical" prefix within the bucket.
    return f"canonical/{version_id}/manifest.json"


def _object_is_absent(key: str) -> bool:
    try:
        _head_object(key)
    except ClientError as exc:
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if status == 404:
            return True
        raise
    return False


def _waveform(client: httpx.Client, recording_id: str) -> dict[str, Any]:
    waveform = dict(
        _json(
            client.get(
                f"/api/recordings/{recording_id}/waveform",
                params={
                    "start_seconds": 0,
                    "duration_seconds": 2,
                    "channel_index": 0,
                    "pixel_width": 256,
                },
            ),
            200,
            "load waveform",
        )
    )
    channels = waveform.get("channels")
    if (
        not isinstance(channels, list)
        or not channels
        or not channels[0].get("minimum")
        or not channels[0].get("maximum")
    ):
        raise RuntimeError("waveform response did not contain persisted signal data")
    return waveform


def _validate_ready_recording(
    client: httpx.Client, recording_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    recording = dict(_json(client.get(f"/api/recordings/{recording_id}"), 200, "read recording"))
    if recording["state"] != "READY" or not recording["active_version_id"]:
        raise RuntimeError(f"recording is not ready: {recording}")
    metadata = dict(recording["safe_metadata"])
    if int(metadata.get("channel_count", 0)) < 1 or int(metadata.get("sample_count", 0)) < 2:
        raise RuntimeError(f"recording metadata is incomplete: {metadata}")
    channels = list(
        _json(
            client.get(f"/api/recordings/{recording_id}/channels"),
            200,
            "list channels",
        )
    )
    if len(channels) != int(metadata["channel_count"]):
        raise RuntimeError("channel rows did not match recording metadata")
    _waveform(client, recording_id)
    qc = dict(_json(client.get(f"/api/recordings/{recording_id}/qc"), 200, "read QC"))
    if not qc.get("metrics"):
        raise RuntimeError("QC did not contain metrics")
    return recording, [dict(item) for item in channels]


def _create_annotation(client: httpx.Client, recording_id: str) -> dict[str, Any]:
    return dict(
        _json(
            client.post(
                f"/api/recordings/{recording_id}/annotations",
                json={
                    "onset_seconds": 0.25,
                    "duration_seconds": 0.25,
                    "label": "Compose persistence marker",
                    "description": "Created by the external-alpha validation gate.",
                },
            ),
            201,
            "create annotation",
        )
    )


def _mark_bad_channel(
    client: httpx.Client, recording_id: str, channel_id: str, revision: int = 0
) -> dict[str, Any]:
    return dict(
        _json(
            client.put(
                f"/api/recordings/{recording_id}/bad-channels/{channel_id}",
                json={
                    "state": "confirmed_bad",
                    "reason": "Compose validation marker",
                    "revision": revision,
                },
            ),
            200,
            "mark bad channel",
        )
    )


def _create_export(client: httpx.Client, recording_id: str) -> tuple[dict[str, Any], bytes, str]:
    export = dict(
        _json(
            client.post(
                f"/api/recordings/{recording_id}/exports",
                json={"format_id": "provenance_json"},
            ),
            201,
            "create provenance export",
        )
    )
    if export["status"] != "READY" or not export["sha256"]:
        raise RuntimeError(f"export was not ready: {export}")
    content = _expect(
        client.get(f"/api/exports/{export['id']}/content"), 200, "download provenance export"
    ).content
    payload = json.loads(content)
    if payload["recording"]["id"] != recording_id:
        raise RuntimeError("provenance export described a different recording")
    if _sha256_bytes(content) != export["sha256"]:
        raise RuntimeError("downloaded export checksum did not match persisted metadata")
    key = f"exports/{recording_id}/{export['id']}.json"
    _head_object(key)
    return export, content, key


def _assert_authorization(
    *,
    workspace_id: str,
    project_id: str,
    recording_id: str,
    export_id: str,
    source_key: str,
) -> None:
    with _client() as outsider:
        _login(outsider, "compose-user-b@example.test", "Compose User B")
        targets = [
            f"/api/workspaces/{workspace_id}/projects",
            f"/api/projects/{project_id}/recordings",
            f"/api/recordings/{recording_id}",
            f"/api/recordings/{recording_id}/waveform",
            f"/api/recordings/{recording_id}/annotations",
            f"/api/exports/{export_id}/content",
        ]
        for target in targets:
            response = outsider.get(target)
            if response.status_code != 404:
                raise RuntimeError(
                    f"cross-user authorization failed for {target}: HTTP {response.status_code}"
                )
    unsigned_source_url = f"{S3_ORIGIN}/{_bucket()}/{source_key}"
    response = httpx.get(unsigned_source_url, timeout=REQUEST_TIMEOUT_SECONDS)
    if response.status_code not in {401, 403, 404}:
        raise RuntimeError(
            f"private source object was anonymously readable: {response.status_code}"
        )


def prepare(state_path: Path) -> None:
    with _client() as client:
        user = _login(client, "compose-user-a@example.test", "Compose User A")
        workspace = dict(
            _json(
                client.post("/api/workspaces", json={"name": "Compose persistence workspace"}),
                201,
                "create workspace",
            )
        )
        project = dict(
            _json(
                client.post(
                    f"/api/workspaces/{workspace['id']}/projects",
                    json={"name": "Real EEG validation", "description": "Compose release gate"},
                ),
                201,
                "create project",
            )
        )
        seeded_key, real_data = _download_seeded_real_sample()
        spec = _sample_spec()
        if len(real_data) != int(spec["size_bytes"]) or _sha256_bytes(real_data) != spec["sha256"]:
            raise RuntimeError("real public EEG bytes did not match the pinned manifest")
        uploaded = _upload(
            client,
            project_id=project["id"],
            name="Compose OpenNeuro persistence EEG",
            filename="openneuro-compose-validation.set",
            data=real_data,
            format_hint="eeglab",
        )
        job = _wait_job(client, uploaded["job_id"], expected="SUCCEEDED")
        recording, channels = _validate_ready_recording(client, uploaded["recording_id"])
        annotation = _create_annotation(client, recording["id"])
        bad_channel = _mark_bad_channel(client, recording["id"], channels[0]["id"])
        export, export_content, export_key = _create_export(client, recording["id"])
        _head_object(uploaded["source_key"])
        _head_object(_canonical_manifest_key(recording["active_version_id"]))
        cookie_name = _env_file_values()["SESSION_COOKIE_NAME"]
        cookie = client.cookies.get(cookie_name)
        if not cookie:
            raise RuntimeError("development session cookie was not issued")
        _assert_authorization(
            workspace_id=workspace["id"],
            project_id=project["id"],
            recording_id=recording["id"],
            export_id=export["id"],
            source_key=uploaded["source_key"],
        )
        state: dict[str, Any] = {
            "user": user,
            "session_cookie": cookie,
            "workspace_id": workspace["id"],
            "project_id": project["id"],
            "recording_id": recording["id"],
            "active_version_id": recording["active_version_id"],
            "recording_state": recording["state"],
            "annotation_id": annotation["id"],
            "bad_channel_id": bad_channel["id"],
            "bad_channel_channel_id": channels[0]["id"],
            "bad_channel_revision": bad_channel["revision"],
            "export_id": export["id"],
            "export_key": export_key,
            "export_sha256": _sha256_bytes(export_content),
            "source_key": uploaded["source_key"],
            "seeded_source_key": seeded_key,
            "ingestion_job_id": uploaded["job_id"],
            "ingestion_attempt": job["attempt"],
            "sample_sha256": spec["sha256"],
            "sample_size_bytes": spec["size_bytes"],
        }
        _save_state(state_path, state)
    print("PASSED: real EEG, persistence fixtures, storage, QC, annotation, export, authorization")


def verify_persistence(state_path: Path) -> None:
    state = _read_state(state_path)
    with _client(cookie=str(state["session_cookie"])) as client:
        session = dict(_json(client.get("/api/session"), 200, "restore signed session"))
        if session["user"]["id"] != state["user"]["id"]:
            raise RuntimeError("session resolved to a different persisted user")
        workspaces = list(_json(client.get("/api/workspaces"), 200, "list workspaces"))
        if state["workspace_id"] not in {item["id"] for item in workspaces}:
            raise RuntimeError("workspace did not persist")
        projects = list(
            _json(
                client.get(f"/api/workspaces/{state['workspace_id']}/projects"),
                200,
                "list projects after restart",
            )
        )
        if state["project_id"] not in {item["id"] for item in projects}:
            raise RuntimeError("project did not persist")
        recordings = list(
            _json(
                client.get(f"/api/projects/{state['project_id']}/recordings"),
                200,
                "list recordings after restart",
            )["items"]
        )
        if state["recording_id"] not in {item["id"] for item in recordings}:
            raise RuntimeError("recording did not persist")
        recording, _ = _validate_ready_recording(client, str(state["recording_id"]))
        if recording["active_version_id"] != state["active_version_id"]:
            raise RuntimeError("active canonical version changed across restart")
        annotations = list(
            _json(
                client.get(f"/api/recordings/{state['recording_id']}/annotations"),
                200,
                "list annotations after restart",
            )["items"]
        )
        if state["annotation_id"] not in {item["id"] for item in annotations}:
            raise RuntimeError("annotation did not persist")
        bad_channel = _mark_bad_channel(
            client,
            str(state["recording_id"]),
            str(state["bad_channel_channel_id"]),
            int(state["bad_channel_revision"]),
        )
        if int(bad_channel["revision"]) != int(state["bad_channel_revision"]) + 1:
            raise RuntimeError("bad-channel review row did not persist")
        export_content = _expect(
            client.get(f"/api/exports/{state['export_id']}/content"),
            200,
            "download export after restart",
        ).content
        if _sha256_bytes(export_content) != state["export_sha256"]:
            raise RuntimeError("export changed across restart")
        _head_object(str(state["source_key"]))
        _head_object(str(state["export_key"]))
        state["bad_channel_revision"] = bad_channel["revision"]
        _save_state(state_path, state)
    print("PASSED: session, database rows, source, canonical waveform, QC, annotation, export")


def _csv_bytes(rows: int, *, invalid: bool = False) -> bytes:
    lines = ["time,C3,C4"]
    for index in range(rows):
        c3 = "not-a-number" if invalid and index == 2 else str(index / 1_000_000)
        lines.append(f"{index / 256:.9f},{c3},{-index / 1_000_000}")
    return ("\n".join(lines) + "\n").encode()


def _csv_config() -> dict[str, Any]:
    return {
        "sampling_frequency_hz": 256,
        "time_column": "time",
        "channel_columns": ["C3", "C4"],
        "channel_names": ["C3", "C4"],
        "units": ["V", "V"],
        "channel_types": ["eeg", "eeg"],
        "delimiter": ",",
    }


def queue_job(state_path: Path) -> None:
    state = _read_state(state_path)
    with _client(cookie=str(state["session_cookie"])) as client:
        uploaded = _upload(
            client,
            project_id=str(state["project_id"]),
            name="Queued worker-restart recording",
            filename="queued-worker-restart.csv",
            data=_csv_bytes(75_000),
            format_hint="csv",
            csv_config=_csv_config(),
        )
        job = dict(_json(client.get(f"/api/jobs/{uploaded['job_id']}"), 200, "read queued job"))
        if job["status"] != "QUEUED" or int(job["attempt"]) != 0:
            raise RuntimeError(f"job was not safely queued while the worker was stopped: {job}")
        state["queued"] = uploaded
        _save_state(state_path, state)
    print("PASSED: ingestion remained QUEUED with worker stopped")


def _concurrent_waveforms(cookie: str, recording_id: str) -> None:
    def request_waveform(_: int) -> int:
        with _client(cookie=cookie) as client:
            response = client.get(
                f"/api/recordings/{recording_id}/waveform",
                params={"channel_index": 0, "duration_seconds": 2, "pixel_width": 256},
            )
            return response.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(request_waveform, range(16)))
    if statuses != [200] * len(statuses):
        raise RuntimeError(f"API failed concurrent waveform sanity requests: {statuses}")


def finish_queued_job(state_path: Path) -> None:
    state = _read_state(state_path)
    queued = dict(state["queued"])
    with _client(cookie=str(state["session_cookie"])) as client:
        _concurrent_waveforms(str(state["session_cookie"]), str(state["recording_id"]))
        job = _wait_job(client, str(queued["job_id"]), expected="SUCCEEDED")
        if int(job["attempt"]) != 1:
            raise RuntimeError(f"queued ingestion executed an unexpected number of attempts: {job}")
        recording, channels = _validate_ready_recording(client, str(queued["recording_id"]))
        annotation = _create_annotation(client, recording["id"])
        bad_channel = _mark_bad_channel(client, recording["id"], channels[0]["id"])
        export, _, export_key = _create_export(client, recording["id"])
        processing = dict(
            _json(
                client.post(
                    f"/api/recordings/{recording['id']}/processing-runs",
                    json={
                        "operation_id": "average_reference",
                        "parameters": {"channel_indices": [0, 1]},
                    },
                ),
                202,
                "create derived processing run",
            )
        )
        _wait_job(client, processing["job_id"], expected="SUCCEEDED")
        runs = list(
            _json(
                client.get(f"/api/recordings/{recording['id']}/processing-runs"),
                200,
                "list processing runs",
            )
        )
        run = next(dict(item) for item in runs if item["id"] == processing["id"])
        if run["status"] != "SUCCEEDED" or not run["output_version_id"]:
            raise RuntimeError(f"derived processing output was not persisted: {run}")
        _head_object(str(queued["source_key"]))
        state["disposable"] = {
            **queued,
            "active_version_id": recording["active_version_id"],
            "derived_version_id": run["output_version_id"],
            "annotation_id": annotation["id"],
            "bad_channel_id": bad_channel["id"],
            "export_id": export["id"],
            "export_key": export_key,
        }
        _save_state(state_path, state)
    print("PASSED: queued job survived, worker processed it, API stayed responsive under load")


def failure_and_deletion(state_path: Path) -> None:
    state = _read_state(state_path)
    disposable = dict(state["disposable"])
    with _client(cookie=str(state["session_cookie"])) as client:
        failed = _upload(
            client,
            project_id=str(state["project_id"]),
            name="Expected safe ingestion failure",
            filename="expected-failure.csv",
            data=_csv_bytes(4, invalid=True),
            format_hint="csv",
            csv_config=_csv_config(),
        )
        failed_job = _wait_job(client, failed["job_id"], expected="FAILED")
        failed_recording = dict(
            _json(
                client.get(f"/api/recordings/{failed['recording_id']}"),
                200,
                "read failed recording",
            )
        )
        if (
            failed_recording["state"] != "FAILED"
            or failed_recording["active_version_id"] is not None
            or not failed_recording["error_code"]
            or not failed_job["diagnostic_id"]
        ):
            raise RuntimeError(f"failed ingestion did not stop safely: {failed_recording}")
        deletion = dict(
            _json(
                client.delete(f"/api/recordings/{disposable['recording_id']}"),
                202,
                "request recording deletion",
            )
        )
        _wait_job(client, deletion["id"], expected="SUCCEEDED")
        _expect(
            client.get(f"/api/recordings/{disposable['recording_id']}"),
            404,
            "verify deleted recording is hidden",
        )
        if not _object_is_absent(str(disposable["source_key"])):
            raise RuntimeError("raw source object remained after deletion")
        if not _object_is_absent(str(disposable["export_key"])):
            raise RuntimeError("export object remained after deletion")
        for key in ("active_version_id", "derived_version_id"):
            if not _object_is_absent(_canonical_manifest_key(str(disposable[key]))):
                raise RuntimeError(f"canonical {key} remained after deletion")
        state["failed"] = {
            **failed,
            "status": failed_job["status"],
            "attempt": failed_job["attempt"],
            "diagnostic_id": failed_job["diagnostic_id"],
            "error_code": failed_recording["error_code"],
        }
        state["deletion_job_id"] = deletion["id"]
        _save_state(state_path, state)
    print("PASSED: safe failed job and supported deletion flow")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["prepare", "verify-persistence", "queue-job", "finish-queued", "cleanup"],
    )
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.state)
    elif args.command == "verify-persistence":
        verify_persistence(args.state)
    elif args.command == "queue-job":
        queue_job(args.state)
    elif args.command == "finish-queued":
        finish_queued_job(args.state)
    elif args.command == "cleanup":
        failure_and_deletion(args.state)


if __name__ == "__main__":
    main()
