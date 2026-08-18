"""Private object storage abstraction for immutable scientific assets."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

import boto3
from botocore.client import Config

from neuro_workspace.config import Settings
from neuro_workspace.errors import NotFoundError, ValidationError


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size_bytes: int
    sha256: str | None = None


class ObjectStore(Protocol):
    def put_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> ObjectInfo: ...

    def put_file(self, key: str, path: Path) -> ObjectInfo: ...

    def copy_to_path(self, key: str, destination: Path) -> ObjectInfo: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def list_prefix(self, prefix: str) -> Iterator[ObjectInfo]: ...

    def presign_put(self, key: str, expires_seconds: int = 900) -> str | None: ...

    def read_bytes(self, key: str, max_bytes: int = 32 * 1024 * 1024) -> bytes: ...


def _safe_key(key: str) -> str:
    normalized = key.strip("/")
    if not normalized or ".." in Path(normalized).parts or Path(normalized).is_absolute():
        raise ValidationError("unsafe_object_key", "The generated storage key is invalid.")
    return normalized


class LocalObjectStore:
    """Filesystem implementation used by tests and native development."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        destination = (self.root / _safe_key(key)).resolve()
        if self.root not in destination.parents and destination != self.root:
            raise ValidationError("unsafe_object_key", "The generated storage key is invalid.")
        return destination

    def put_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> ObjectInfo:
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".partial")
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValidationError(
                            "too_large", "The uploaded file exceeds the size limit."
                        )
                    digest.update(chunk)
                    output.write(chunk)
            temporary.replace(destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return ObjectInfo(key, total, digest.hexdigest())

    def put_file(self, key: str, path: Path) -> ObjectInfo:
        with path.open("rb") as stream:
            return self.put_stream(key, stream, path.stat().st_size + 1)

    def copy_to_path(self, key: str, destination: Path) -> ObjectInfo:
        source = self.path_for(key)
        if not source.is_file():
            raise NotFoundError()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return ObjectInfo(key, source.stat().st_size, _sha256_path(source))

    def delete_prefix(self, prefix: str) -> int:
        target = self.path_for(prefix)
        if target.is_file():
            target.unlink()
            return 1
        if not target.exists():
            return 0
        count = sum(1 for path in target.rglob("*") if path.is_file())
        shutil.rmtree(target)
        return count

    def list_prefix(self, prefix: str) -> Iterator[ObjectInfo]:
        target = self.path_for(prefix)
        if target.is_file():
            yield ObjectInfo(prefix, target.stat().st_size)
            return
        if target.exists():
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    yield ObjectInfo(str(path.relative_to(self.root)), path.stat().st_size)

    def presign_put(self, key: str, expires_seconds: int = 900) -> str | None:
        del key, expires_seconds
        return None

    def read_bytes(self, key: str, max_bytes: int = 32 * 1024 * 1024) -> bytes:
        path = self.path_for(key)
        if not path.is_file():
            raise NotFoundError()
        if path.stat().st_size > max_bytes:
            raise ValidationError(
                "export_too_large", "This export is too large for direct download."
            )
        return path.read_bytes()


class S3ObjectStore:
    """S3-compatible implementation with private, expiring operations."""

    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        credentials = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": (
                settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None
            ),
        }
        client_config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            config=client_config,
            **credentials,
        )
        self.public_client = (
            boto3.client(
                "s3",
                endpoint_url=settings.s3_public_endpoint_url,
                config=client_config,
                **credentials,
            )
            if settings.s3_public_endpoint_url
            else self.client
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            if settings.environment in {"development", "test"}:
                self.client.create_bucket(Bucket=self.bucket)
            else:
                raise

    def put_stream(self, key: str, stream: BinaryIO, max_bytes: int) -> ObjectInfo:
        # The local proxy is bounded; large clients use presigned multipart uploads.
        digest = hashlib.sha256()
        total = 0
        chunks: list[bytes] = []
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise ValidationError("too_large", "The uploaded file exceeds the size limit.")
            digest.update(chunk)
            chunks.append(chunk)
        self.client.put_object(Bucket=self.bucket, Key=_safe_key(key), Body=b"".join(chunks))
        return ObjectInfo(key, total, digest.hexdigest())

    def put_file(self, key: str, path: Path) -> ObjectInfo:
        key = _safe_key(key)
        self.client.upload_file(str(path), self.bucket, key)
        return ObjectInfo(key, path.stat().st_size, _sha256_path(path))

    def copy_to_path(self, key: str, destination: Path) -> ObjectInfo:
        key = _safe_key(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))
        return ObjectInfo(key, destination.stat().st_size, _sha256_path(destination))

    def delete_prefix(self, prefix: str) -> int:
        keys = [item.key for item in self.list_prefix(prefix)]
        for offset in range(0, len(keys), 1000):
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in keys[offset : offset + 1000]]},
            )
        return len(keys)

    def list_prefix(self, prefix: str) -> Iterator[ObjectInfo]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=_safe_key(prefix)):
            for item in page.get("Contents", []):
                yield ObjectInfo(str(item["Key"]), int(item["Size"]))

    def presign_put(self, key: str, expires_seconds: int = 900) -> str | None:
        return str(
            self.public_client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": _safe_key(key)},
                ExpiresIn=expires_seconds,
            )
        )

    def read_bytes(self, key: str, max_bytes: int = 32 * 1024 * 1024) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))
        length = int(response.get("ContentLength", 0))
        if length > max_bytes:
            response["Body"].close()
            raise ValidationError(
                "export_too_large", "This export is too large for direct download."
            )
        data = response["Body"].read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValidationError(
                "export_too_large", "This export is too large for direct download."
            )
        return bytes(data)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_object_store(settings: Settings) -> ObjectStore:
    if settings.object_store_backend == "s3":
        return S3ObjectStore(settings)
    return LocalObjectStore(settings.object_store_root)
