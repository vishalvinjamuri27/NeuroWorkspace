"""Framework-independent signal domain types and policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Capability(StrEnum):
    READ = "read"
    CREATE = "create"
    ANNOTATE = "annotate"
    PROCESS = "process"
    EXPORT = "export"
    DELETE_RECORDING = "delete_recording"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_WORKSPACE = "manage_workspace"


ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.OWNER: frozenset(Capability),
    Role.ADMIN: frozenset(Capability),
    Role.MEMBER: frozenset(
        {
            Capability.READ,
            Capability.CREATE,
            Capability.ANNOTATE,
            Capability.PROCESS,
            Capability.EXPORT,
            Capability.DELETE_RECORDING,
        }
    ),
    Role.VIEWER: frozenset({Capability.READ, Capability.EXPORT}),
}


class RecordingState(StrEnum):
    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    INGESTING = "INGESTING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


class JobKind(StrEnum):
    INGESTION = "INGESTION"
    QC = "QC"
    PROCESSING = "PROCESSING"
    EXPORT = "EXPORT"
    DELETION = "DELETION"


class AssetRole(StrEnum):
    PRIMARY = "primary"
    HEADER = "header"
    MARKER = "marker"
    DATA = "data"
    SIDECAR = "sidecar"
    ANNOTATIONS = "annotations"


@dataclass(frozen=True, slots=True)
class ChannelMetadata:
    index: int
    name: str
    source_name: str
    channel_type: str
    unit: str
    source_unit: str | None = None
    conversion_to_si: float = 1.0
    source_sampling_frequency_hz: float | None = None


@dataclass(frozen=True, slots=True)
class EventMetadata:
    onset_seconds: float
    duration_seconds: float
    label: str
    code: str | None = None
    source_index: int | None = None


@dataclass(frozen=True, slots=True)
class RecordingMetadata:
    format_id: str
    sampling_frequency_hz: float
    sample_count: int
    duration_seconds: float
    channels: tuple[ChannelMetadata, ...]
    events: tuple[EventMetadata, ...] = ()
    safe_extra: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SignalChunk:
    start_sample: int
    values: NDArray[np.float32]

    @property
    def stop_sample(self) -> int:
        return self.start_sample + int(self.values.shape[1])


@dataclass(frozen=True, slots=True)
class AssetProbe:
    name: str
    path: str
    size_bytes: int
    role: AssetRole = AssetRole.PRIMARY


@dataclass(frozen=True, slots=True)
class Detection:
    format_id: str
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class CSVImportConfig:
    sampling_frequency_hz: float | None
    time_column: str | None
    channel_columns: tuple[str, ...]
    channel_names: tuple[str, ...]
    units: tuple[str, ...]
    channel_types: tuple[str, ...] = ()
    delimiter: str = ","


ImportConfig = CSVImportConfig | None
