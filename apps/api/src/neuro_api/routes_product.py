"""Privacy-safe product evidence and user feedback routes."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from neuro_api.runtime import Runtime, current_user, get_db, get_runtime, optional_identity
from neuro_api.schemas import (
    FeedbackAdminResponse,
    FeedbackCreate,
    FeedbackResponse,
    ProductEventCreate,
    ProductMetricsResponse,
)
from neuro_workspace.auth import Identity
from neuro_workspace.db.models import Feedback, ProductEvent, Recording, User
from neuro_workspace.domain import Capability
from neuro_workspace.errors import NeuroError
from neuro_workspace.product_analytics import track_product_event
from neuro_workspace.services.access import ensure_user, recording_for_user, require_access

router = APIRouter()


def _optional_user(identity: Identity | None, session: Session) -> User | None:
    if identity is None:
        return None
    return ensure_user(
        session,
        subject=identity.subject,
        email=identity.email,
        display_name=identity.display_name,
    )


def _admin(user: User, runtime: Runtime) -> None:
    if user.email.casefold() not in runtime.settings.alpha_admin_email_set:
        raise NeuroError("not_found", "The requested resource was not found.", 404)


@router.post("/product-events", status_code=204)
def create_product_event(
    body: ProductEventCreate,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
) -> None:
    user = _optional_user(identity, session)
    recording: Recording | None = None
    if body.recording_id:
        candidate = session.get(Recording, body.recording_id)
        if candidate is None or candidate.deleted_at is not None:
            raise NeuroError("not_found", "The requested resource was not found.", 404)
        if candidate.is_demo:
            recording = candidate
        elif user is not None:
            recording = recording_for_user(session, user.id, candidate.id)
        else:
            raise NeuroError("authentication_required", "Sign in to continue.", 401)

    viewer_events = {
        "viewer_zoomed",
        "viewer_panned",
        "channel_visibility_changed",
        "qc_opened",
    }
    if body.name == "sample_demo_started" and recording is not None:
        raise NeuroError("invalid_product_event", "This event does not accept a recording.", 422)
    if body.name == "sample_recording_opened" and (recording is None or not recording.is_demo):
        raise NeuroError("not_found", "The requested resource was not found.", 404)
    if body.name == "recording_opened" and (user is None or recording is None or recording.is_demo):
        raise NeuroError("authentication_required", "Sign in to continue.", 401)
    if body.name in viewer_events and recording is None:
        raise NeuroError("not_found", "The requested resource was not found.", 404)
    if user is None and body.name not in {
        "sample_demo_started",
        "sample_recording_opened",
        *viewer_events,
    }:
        raise NeuroError("authentication_required", "Sign in to continue.", 401)
    if user is None and body.name in viewer_events and (recording is None or not recording.is_demo):
        raise NeuroError("not_found", "The requested resource was not found.", 404)

    event_name: str = body.name
    properties: dict[str, str] | None = None
    if event_name == "recording_opened" and recording is not None:
        properties = {"source": "sample" if recording.is_demo else "owned"}
        if user is not None and not recording.is_demo:
            prior_session = session.scalar(
                select(ProductEvent.id).where(
                    ProductEvent.name.in_({"recording_opened", "recording_reopened"}),
                    ProductEvent.actor_id == user.id,
                    ProductEvent.recording_id == recording.id,
                    ProductEvent.session_id != body.session_id,
                )
            )
            if prior_session is not None:
                event_name = "recording_reopened"
    track_product_event(
        session,
        event_name,
        actor_id=user.id if user else None,
        workspace_id=recording.workspace_id if recording else None,
        recording_id=recording.id if recording else None,
        session_id=body.session_id,
        properties=properties,
    )
    session.commit()


@router.post("/feedback", response_model=FeedbackResponse, status_code=201)
def create_feedback(
    body: FeedbackCreate,
    identity: Identity | None = Depends(optional_identity),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> FeedbackResponse:
    user = _optional_user(identity, session)
    workspace_id: str | None = None
    recording_id: str | None = None
    if body.recording_id:
        candidate = session.get(Recording, body.recording_id)
        if candidate is None or candidate.deleted_at is not None:
            raise NeuroError("not_found", "The requested resource was not found.", 404)
        if not candidate.is_demo:
            if user is None:
                raise NeuroError("authentication_required", "Sign in to continue.", 401)
            candidate = recording_for_user(session, user.id, candidate.id)
        recording_id = candidate.id
        workspace_id = candidate.workspace_id
    elif body.workspace_id:
        if user is None:
            raise NeuroError("authentication_required", "Sign in to continue.", 401)
        require_access(session, user.id, body.workspace_id, Capability.READ)
        workspace_id = body.workspace_id

    contact_email = body.contact_email if body.may_contact else None
    if body.may_contact and not contact_email and user is not None:
        contact_email = user.email
    feedback = Feedback(
        actor_id=user.id if user else None,
        workspace_id=workspace_id,
        recording_id=recording_id,
        category=body.category,
        message=body.message.strip(),
        route=body.route,
        app_version=runtime.settings.build_version,
        may_contact=body.may_contact,
        contact_email=contact_email,
    )
    session.add(feedback)
    session.commit()
    return FeedbackResponse.model_validate(feedback)


@router.get("/admin/feedback", response_model=list[FeedbackAdminResponse])
def list_feedback(
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> list[FeedbackAdminResponse]:
    _admin(user, runtime)
    rows = session.scalars(select(Feedback).order_by(Feedback.created_at.desc()).limit(500)).all()
    return [FeedbackAdminResponse.model_validate(row) for row in rows]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


@router.get("/admin/product-metrics", response_model=ProductMetricsResponse)
def product_metrics(
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
    runtime: Runtime = Depends(get_runtime),
) -> ProductMetricsResponse:
    _admin(user, runtime)
    events = session.scalars(select(ProductEvent)).all()
    event_counts = Counter(event.name for event in events)
    unique: dict[str, set[str]] = {}
    for event in events:
        if event.actor_id:
            unique.setdefault(event.name, set()).add(event.actor_id)

    new_users = len(unique.get("signup_completed", set()))
    opened = unique.get("recording_opened", set()) | unique.get("recording_reopened", set())
    upload_starters = unique.get("upload_started", set())
    ingestion_users = unique.get("ingestion_succeeded", set())
    own_openers = {
        event.actor_id
        for event in events
        if event.actor_id
        and event.name in {"recording_opened", "recording_reopened"}
        and event.safe_properties.get("source") == "owned"
    }
    annotators = unique.get("annotation_created", set())
    returners = unique.get("recording_reopened", set())
    # Product events are authoritative; counting actor events avoids filenames and metadata.
    succeeded_by_actor = Counter(
        event.actor_id for event in events if event.name == "ingestion_succeeded" and event.actor_id
    )
    multi = sum(1 for count in succeeded_by_actor.values() if count >= 2)

    format_counts: Counter[str] = Counter()
    size_counts: Counter[str] = Counter()
    for event in events:
        if event.name not in {"upload_started", "ingestion_succeeded", "ingestion_failed"}:
            continue
        if value := event.safe_properties.get("format"):
            format_counts[str(value)] += 1
        if value := event.safe_properties.get("size_bucket"):
            size_counts[str(value)] += 1
    return ProductMetricsResponse(
        event_counts=dict(sorted(event_counts.items())),
        unique_users={name: len(ids) for name, ids in sorted(unique.items())},
        activation_rate=_ratio(len(opened), new_users),
        upload_activation_rate=_ratio(len(ingestion_users), len(upload_starters)),
        annotation_usage_rate=_ratio(len(annotators & own_openers), len(own_openers)),
        return_behavior_rate=_ratio(len(returners), len(own_openers)),
        multi_recording_rate=_ratio(multi, len(ingestion_users)),
        format_counts=dict(sorted(format_counts.items())),
        size_bucket_counts=dict(sorted(size_counts.items())),
    )
