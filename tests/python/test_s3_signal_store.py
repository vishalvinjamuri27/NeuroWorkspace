"""End-to-end proof that canonical signal storage works directly against
S3-compatible object storage, with no shared filesystem between the writer
and the reader.

This is the property the whole Phase-3 storage migration rests on, so it is
tested against a *real* MinIO container (via the Docker CLI already used by
this repo's own docker-compose, not a mocked S3 client) rather than asserted
from code inspection. It is opt-in like the real_data suite -- most
environments running `make test` do not have Docker available to the test
process -- and is skipped unless explicitly requested:

    uv run pytest tests/python/test_s3_signal_store.py -m s3_integration

Run with `-s` to see MinIO's container id if you want to inspect logs after
a failure; the fixture removes the container either way.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import numpy as np
import pytest

from neuro_workspace.domain import ChannelMetadata, RecordingMetadata, SignalChunk

pytestmark = pytest.mark.s3_integration


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True, timeout=5)
    except Exception:
        return False
    return True


@pytest.fixture(scope="module")
def minio_endpoint() -> Iterator[dict[str, str]]:
    if not _docker_available():
        pytest.skip("Docker is not available to the test process")

    name = f"nw-test-minio-{uuid.uuid4().hex[:8]}"
    port = 19100
    access_key, secret_key, bucket = "neuroworkspace-test", "neuroworkspace-test-secret", "nw-test"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"{port}:9000",
            "-e",
            f"MINIO_ROOT_USER={access_key}",
            "-e",
            f"MINIO_ROOT_PASSWORD={secret_key}",
            "cgr.dev/chainguard/minio:latest@sha256:73297936408b731668ca0e835ac9add1a35cb6026a125c2f0cd476d9ef48222c",
            "server",
            "/data",
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"Could not start MinIO container: {started.stderr}")

    endpoint_url = f"http://localhost:{port}"
    try:
        import boto3
        from botocore.client import Config

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                client.create_bucket(Bucket=bucket)
                break
            except Exception as exc:  # MinIO still booting
                last_error = exc
                time.sleep(1)
        else:
            pytest.skip(f"MinIO did not become ready in time: {last_error}")

        yield {
            "endpoint_url": endpoint_url,
            "access_key": access_key,
            "secret_key": secret_key,
            "bucket": bucket,
        }
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _settings(minio_endpoint: dict[str, str]):
    from neuro_workspace.config import Settings

    return Settings(
        environment="test",
        object_store_backend="s3",
        s3_endpoint_url=minio_endpoint["endpoint_url"],
        s3_access_key=minio_endpoint["access_key"],
        s3_secret_key=minio_endpoint["secret_key"],
        s3_bucket=minio_endpoint["bucket"],
        session_secret="test-session-secret-that-is-long-enough",
    )


def _metadata(samples: int = 2000) -> RecordingMetadata:
    return RecordingMetadata(
        format_id="test",
        sampling_frequency_hz=100.0,
        sample_count=samples,
        duration_seconds=samples / 100.0,
        channels=(
            ChannelMetadata(0, "C3", "C3", "eeg", "V"),
            ChannelMetadata(1, "C4", "C4", "eeg", "V"),
        ),
    )


def test_write_from_one_store_instance_reads_correctly_from_a_second(
    minio_endpoint: dict[str, str],
) -> None:
    """The core no-shared-filesystem claim: a value written by one
    S3ZarrSignalStore instance (simulating one worker replica) is read back
    correctly by a completely separate instance (simulating a second API
    replica) that shares nothing but the bucket.
    """

    from neuro_workspace.signal_store import S3ZarrSignalStore

    settings = _settings(minio_endpoint)
    writer = S3ZarrSignalStore(settings)
    metadata = _metadata()
    rng = np.random.default_rng(7)
    values = rng.normal(0, 1, size=(2, metadata.sample_count)).astype(np.float32)
    version_id = f"v{uuid.uuid4().hex}"

    manifest = writer.write("test-store-" + version_id, metadata, iter([SignalChunk(0, values)]))
    version_id = "test-store-" + version_id
    assert manifest.pyramid_levels >= 1

    reader = S3ZarrSignalStore(settings)  # a second, independent instance
    assert reader.exists(version_id)
    window = reader.read_raw_window(version_id, 100, 110)
    assert np.allclose(window, values[:, 100:110])

    viewport = reader.query(version_id, [0, 1], 0.0, 5.0, 200)
    assert len(viewport.channels) == 2
    assert viewport.channels[0].minimum  # non-empty bins came back


def test_delete_removes_every_object_under_the_version_prefix(
    minio_endpoint: dict[str, str],
) -> None:
    from neuro_workspace.signal_store import S3ZarrSignalStore

    settings = _settings(minio_endpoint)
    store = S3ZarrSignalStore(settings)
    metadata = _metadata(samples=500)
    values = np.zeros((2, metadata.sample_count), dtype=np.float32)
    version_id = f"delete-test-{uuid.uuid4().hex}"

    store.write(version_id, metadata, iter([SignalChunk(0, values)]))
    assert store.exists(version_id)

    removed = store.delete(version_id)
    assert removed > 0
    assert not store.exists(version_id)
    # Idempotent: deleting again is a clean no-op, not an error.
    assert store.delete(version_id) == 0


def test_writing_to_an_existing_version_id_is_rejected(minio_endpoint: dict[str, str]) -> None:
    from neuro_workspace.errors import ValidationError
    from neuro_workspace.signal_store import S3ZarrSignalStore

    settings = _settings(minio_endpoint)
    store = S3ZarrSignalStore(settings)
    metadata = _metadata(samples=200)
    values = np.zeros((2, metadata.sample_count), dtype=np.float32)
    version_id = f"immutable-test-{uuid.uuid4().hex}"

    store.write(version_id, metadata, iter([SignalChunk(0, values)]))
    with pytest.raises(ValidationError, match="already exists"):
        store.write(version_id, metadata, iter([SignalChunk(0, values)]))


def test_a_failed_write_leaves_no_partial_version_visible(minio_endpoint: dict[str, str]) -> None:
    """If the chunk producer raises partway through, exists() must return
    False afterward -- an interrupted write must never look like a
    completed, queryable version.
    """

    from neuro_workspace.signal_store import S3ZarrSignalStore

    settings = _settings(minio_endpoint)
    store = S3ZarrSignalStore(settings)
    metadata = _metadata(samples=1000)
    version_id = f"partial-write-{uuid.uuid4().hex}"

    def broken_chunks() -> Iterator[SignalChunk]:
        yield SignalChunk(0, np.zeros((2, 400), dtype=np.float32))
        raise RuntimeError("adapter crashed mid-stream")

    with pytest.raises(RuntimeError, match="adapter crashed"):
        store.write(version_id, metadata, broken_chunks())

    assert not store.exists(version_id)
