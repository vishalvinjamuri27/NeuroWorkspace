"""Allowlisted, PHI-resistant product analytics helpers."""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy.orm import Session

from neuro_workspace.db.models import ProductEvent

EVENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "sample_demo_started",
        "sample_recording_opened",
        "signup_completed",
        "workspace_created",
        "project_created",
        "upload_started",
        "upload_completed",
        "upload_failed",
        "ingestion_started",
        "ingestion_succeeded",
        "ingestion_failed",
        "recording_opened",
        "viewer_zoomed",
        "viewer_panned",
        "channel_visibility_changed",
        "annotation_created",
        "annotation_updated",
        "annotation_deleted",
        "bad_channel_marked",
        "qc_opened",
        "processing_run_created",
        "export_started",
        "export_completed",
        "export_failed",
        "recording_reopened",
    }
)

FORMAT_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"edf", "bdf", "brainvision", "eeglab", "fif", "csv", "unknown", "synthetic"}
)
SIZE_BUCKETS: Final[frozenset[str]] = frozenset(
    {"<100 MB", "100-500 MB", "500 MB-2 GB", "2-10 GB", ">10 GB", "unknown"}
)
EXPORT_FORMATS: Final[frozenset[str]] = frozenset({"annotations_csv", "provenance_json", "qc_json"})

_PROPERTY_ALLOWLIST: Final[dict[str, frozenset[str]]] = {
    "upload_started": frozenset({"format", "size_bucket"}),
    "upload_completed": frozenset({"format", "size_bucket"}),
    "upload_failed": frozenset({"format", "size_bucket", "failure_class"}),
    "ingestion_started": frozenset({"format", "size_bucket"}),
    "ingestion_succeeded": frozenset({"format", "size_bucket"}),
    "ingestion_failed": frozenset({"format", "size_bucket", "failure_class"}),
    "recording_opened": frozenset({"source"}),
    "recording_reopened": frozenset({"source"}),
    "processing_run_created": frozenset({"operation_id"}),
    "export_started": frozenset({"format"}),
    "export_completed": frozenset({"format"}),
    "export_failed": frozenset({"format", "failure_class"}),
}


def size_bucket(total_bytes: int | None) -> str:
    if total_bytes is None or total_bytes < 0:
        return "unknown"
    mib = 1024 * 1024
    gib = 1024 * mib
    if total_bytes < 100 * mib:
        return "<100 MB"
    if total_bytes < 500 * mib:
        return "100-500 MB"
    if total_bytes < 2 * gib:
        return "500 MB-2 GB"
    if total_bytes < 10 * gib:
        return "2-10 GB"
    return ">10 GB"


def format_category(value: str | None) -> str:
    normalized = (value or "unknown").casefold()
    return normalized if normalized in FORMAT_CATEGORIES else "unknown"


def _validated_properties(name: str, properties: dict[str, Any] | None) -> dict[str, Any]:
    supplied = properties or {}
    allowed = _PROPERTY_ALLOWLIST.get(name, frozenset())
    if set(supplied) - allowed:
        raise ValueError("product event includes a non-allowlisted property")
    result: dict[str, Any] = {}
    for key, raw in supplied.items():
        if not isinstance(raw, str) or len(raw) > 80:
            raise ValueError("product event properties must be short strings")
        value = raw.casefold() if key in {"format", "source"} else raw
        if key == "format":
            if name.startswith("export_"):
                if value not in EXPORT_FORMATS:
                    raise ValueError("invalid export format category")
            elif value not in FORMAT_CATEGORIES:
                raise ValueError("invalid recording format category")
        elif key == "size_bucket" and value not in SIZE_BUCKETS:
            raise ValueError("invalid size bucket")
        elif key == "source" and value not in {"sample", "owned"}:
            raise ValueError("invalid recording source")
        elif key in {"failure_class", "operation_id"}:
            value = value[:80]
        result[key] = value
    return result


def track_product_event(
    session: Session,
    name: str,
    *,
    actor_id: str | None = None,
    workspace_id: str | None = None,
    recording_id: str | None = None,
    session_id: str | None = None,
    properties: dict[str, Any] | None = None,
) -> ProductEvent:
    if name not in EVENT_NAMES:
        raise ValueError("unknown product event")
    event = ProductEvent(
        name=name,
        actor_id=actor_id,
        workspace_id=workspace_id,
        recording_id=recording_id,
        session_id=session_id[:64] if session_id else None,
        safe_properties=_validated_properties(name, properties),
    )
    session.add(event)
    return event
