"""Relational metadata model; large scientific arrays stay in object storage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from neuro_workspace.domain import AssetRole, JobKind, JobState, RecordingState, Role


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    external_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), default=Role.MEMBER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_workspace_created", "workspace_id", "created_at", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")


class Upload(TimestampMixin, Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="CREATED")
    format_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    csv_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceAsset(TimestampMixin, Base):
    __tablename__ = "source_assets"
    __table_args__ = (UniqueConstraint("upload_id", "safe_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"))
    recording_id: Mapped[str | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), nullable=True
    )
    object_key: Mapped[str] = mapped_column(String(500), unique=True)
    safe_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[AssetRole] = mapped_column(Enum(AssetRole, native_enum=False))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Recording(TimestampMixin, Base):
    __tablename__ = "recordings"
    __table_args__ = (
        Index("ix_recordings_project_created", "workspace_id", "project_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(180))
    format_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[RecordingState] = mapped_column(
        Enum(RecordingState, native_enum=False), default=RecordingState.CREATED, index=True
    )
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecordingVersion(TimestampMixin, Base):
    __tablename__ = "recording_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("recording_versions.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(30), default="normalized_raw")
    store_key: Mapped[str] = mapped_column(String(500), unique=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sampling_frequency_hz: Mapped[float] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float] = mapped_column(Float)
    channel_count: Mapped[int] = mapped_column(Integer)
    software: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("version_id", "channel_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("recording_versions.id", ondelete="CASCADE"), index=True
    )
    channel_index: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(160))
    source_name: Mapped[str] = mapped_column(String(160))
    channel_type: Mapped[str] = mapped_column(String(40))
    unit: Mapped[str] = mapped_column(String(20))
    source_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    conversion_to_si: Mapped[float] = mapped_column(Float, default=1.0)
    source_sampling_frequency_hz: Mapped[float | None] = mapped_column(Float, nullable=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_version_onset", "version_id", "onset_seconds"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("recording_versions.id", ondelete="CASCADE"))
    onset_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(String(255))
    code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_index: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Annotation(TimestampMixin, Base):
    __tablename__ = "annotations"
    __table_args__ = (
        CheckConstraint("onset_seconds >= 0"),
        CheckConstraint("duration_seconds >= 0"),
        Index("ix_annotations_recording_onset", "recording_id", "onset_seconds"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("recording_versions.id"), nullable=True
    )
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updater_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    onset_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    provenance_kind: Mapped[str] = mapped_column(String(40), default="user")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BadChannel(TimestampMixin, Base):
    __tablename__ = "bad_channels"
    __table_args__ = (UniqueConstraint("recording_id", "version_id", "channel_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(ForeignKey("recording_versions.id", ondelete="CASCADE"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    state: Mapped[str] = mapped_column(String(30), default="confirmed_bad")
    reason: Mapped[str] = mapped_column(String(500), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class QCMetric(TimestampMixin, Base):
    __tablename__ = "qc_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("recording_versions.id", ondelete="CASCADE"))
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id"), nullable=True)
    rule_id: Mapped[str] = mapped_column(String(80))
    rule_version: Mapped[str] = mapped_column(String(40))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(30), default="1")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class QCFlag(TimestampMixin, Base):
    __tablename__ = "qc_flags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    metric_id: Mapped[str] = mapped_column(ForeignKey("qc_metrics.id", ondelete="CASCADE"))
    severity: Mapped[str] = mapped_column(String(20), default="review")
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    comparison: Mapped[str | None] = mapped_column(String(20), nullable=True)
    safe_message: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(30), default="algorithmic")
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key"),
        Index("ix_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    kind: Mapped[JobKind] = mapped_column(Enum(JobKind, native_enum=False))
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[JobState] = mapped_column(
        Enum(JobState, native_enum=False), default=JobState.QUEUED, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(80), default="Queued")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostic_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessingRun(TimestampMixin, Base):
    __tablename__ = "processing_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    input_version_id: Mapped[str] = mapped_column(ForeignKey("recording_versions.id"))
    output_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("recording_versions.id"), nullable=True
    )
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    operation_id: Mapped[str] = mapped_column(String(80))
    operation_version: Mapped[str] = mapped_column(String(40))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")


class Export(TimestampMixin, Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(ForeignKey("recording_versions.id"))
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    format_id: Mapped[str] = mapped_column(String(40))
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loss_notes: Mapped[list[str]] = mapped_column(JSON, default=list)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_workspace_created", "workspace_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(36))
    safe_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
