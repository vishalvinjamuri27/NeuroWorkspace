"""Transport schemas; domain/storage details do not leak through these models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from neuro_workspace.domain import AssetRole, Role


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserResponse(ORMModel):
    id: str
    email: str
    display_name: str


class DevSessionRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=120)


class SessionResponse(BaseModel):
    user: UserResponse


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceResponse(ORMModel):
    id: str
    name: str
    slug: str
    role: Role
    created_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class ProjectResponse(ORMModel):
    id: str
    workspace_id: str
    name: str
    description: str
    created_at: datetime


class CSVConfigRequest(BaseModel):
    sampling_frequency_hz: float | None = Field(default=None, gt=0)
    time_column: str | None = None
    channel_columns: list[str] = Field(min_length=1, max_length=512)
    channel_names: list[str] = Field(min_length=1, max_length=512)
    units: list[str] = Field(min_length=1, max_length=512)
    channel_types: list[str] = Field(default_factory=list, max_length=512)
    delimiter: str = Field(default=",", min_length=1, max_length=1)


class UploadAssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    role: AssetRole = AssetRole.PRIMARY
    media_type: str | None = Field(default=None, max_length=120)


class UploadCreate(BaseModel):
    recording_name: str = Field(min_length=1, max_length=180)
    format_hint: Literal["edf", "bdf", "brainvision", "eeglab", "fif", "csv"] | None = None
    assets: list[UploadAssetCreate] = Field(min_length=1, max_length=16)
    csv_config: CSVConfigRequest | None = None


class UploadAssetResponse(BaseModel):
    id: str
    safe_name: str
    role: AssetRole
    size_bytes: int
    upload_url: str
    direct: bool


class UploadResponse(BaseModel):
    id: str
    status: str
    recording_name: str
    assets: list[UploadAssetResponse]


class UploadCompleteResponse(BaseModel):
    recording_id: str
    job_id: str
    state: str


class RecordingResponse(ORMModel):
    id: str
    workspace_id: str
    project_id: str
    name: str
    format_id: str | None
    state: str
    active_version_id: str | None
    safe_metadata: dict[str, Any]
    source_provenance: dict[str, Any]
    is_demo: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime


class ChannelResponse(ORMModel):
    id: str
    channel_index: int
    name: str
    source_name: str
    channel_type: str
    unit: str
    source_unit: str | None
    source_sampling_frequency_hz: float | None


class EventResponse(ORMModel):
    id: str
    onset_seconds: float
    duration_seconds: float
    label: str
    code: str | None


class AnnotationCreate(BaseModel):
    onset_seconds: float = Field(ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)


class AnnotationUpdate(BaseModel):
    revision: int = Field(ge=1)
    onset_seconds: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)


class AnnotationDelete(BaseModel):
    revision: int = Field(ge=1)


class AnnotationResponse(ORMModel):
    id: str
    recording_id: str
    version_id: str | None
    creator_id: str
    updater_id: str
    onset_seconds: float
    duration_seconds: float
    label: str
    description: str
    provenance_kind: str
    revision: int
    created_at: datetime
    updated_at: datetime


class BadChannelUpdate(BaseModel):
    state: Literal["confirmed_bad", "confirmed_good"]
    reason: str = Field(default="", max_length=500)


class BadChannelResponse(ORMModel):
    id: str
    channel_id: str
    state: str
    reason: str
    actor_id: str
    revision: int


class ProcessingRunCreate(BaseModel):
    operation_id: Literal["average_reference"]
    parameters: dict[str, Any]

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        indices = value.get("channel_indices")
        if not isinstance(indices, list) or not indices or len(indices) > 512:
            raise ValueError("average_reference requires channel_indices")
        if any(not isinstance(item, int) or item < 0 for item in indices):
            raise ValueError("channel_indices must contain nonnegative integers")
        if len(indices) != len(set(indices)):
            raise ValueError("channel_indices must be unique")
        return {"channel_indices": indices}


class ProcessingRunResponse(ORMModel):
    id: str
    recording_id: str
    input_version_id: str
    output_version_id: str | None
    job_id: str
    operation_id: str
    operation_version: str
    parameters: dict[str, Any]
    status: str
    created_at: datetime


class ExportCreate(BaseModel):
    format_id: Literal["annotations_csv", "provenance_json", "qc_json"]


class ExportResponse(ORMModel):
    id: str
    recording_id: str
    version_id: str
    format_id: str
    status: str
    size_bytes: int | None
    sha256: str | None
    loss_notes: list[str]
    created_at: datetime


class JobResponse(ORMModel):
    id: str
    kind: str
    subject_type: str
    subject_id: str
    status: str
    attempt: int
    progress: float
    stage: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: dict[str, Any]


class ErrorResponse(BaseModel):
    error: ErrorBody
