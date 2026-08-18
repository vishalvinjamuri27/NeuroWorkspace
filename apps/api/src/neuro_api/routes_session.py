"""Authentication, workspace, and project routes."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from neuro_api.runtime import Runtime, current_user, get_db, get_runtime
from neuro_api.schemas import (
    DevSessionRequest,
    MembershipResponse,
    MembershipRoleUpdate,
    MembershipUpsert,
    ProjectCreate,
    ProjectResponse,
    SessionResponse,
    UserResponse,
    WorkspaceCreate,
    WorkspaceMetricsResponse,
    WorkspaceResponse,
)
from neuro_workspace.auth import Identity
from neuro_workspace.db.models import (
    Annotation,
    AuditEvent,
    Export,
    Membership,
    Project,
    Recording,
    User,
    Workspace,
)
from neuro_workspace.domain import Capability, RecordingState, Role
from neuro_workspace.errors import NeuroError
from neuro_workspace.product_analytics import track_product_event
from neuro_workspace.services.access import ensure_user, require_access

router = APIRouter()


@router.get("/session/config")
def session_config(runtime: Runtime = Depends(get_runtime)) -> dict[str, str | bool]:
    return {
        "dev_auth_enabled": runtime.settings.dev_auth_enabled
        and runtime.settings.environment != "production",
        "oidc_configured": runtime.oidc is not None and bool(runtime.settings.oidc_client_id),
        "oidc_login_url": (f"{runtime.settings.api_public_url.rstrip('/')}/api/session/oidc/login"),
        "build_version": runtime.settings.build_version,
    }


def _oidc_configuration(runtime: Runtime) -> dict[str, str]:
    if runtime.oidc is None or not runtime.settings.oidc_issuer:
        raise NeuroError("oidc_not_configured", "Hosted sign-in is not configured.", 503)
    try:
        response = httpx.get(
            f"{runtime.settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        issuer = str(payload["issuer"]).rstrip("/")
        if issuer != runtime.settings.oidc_issuer.rstrip("/"):
            raise ValueError("issuer mismatch")
        authorization_endpoint = str(payload["authorization_endpoint"])
        token_endpoint = str(payload["token_endpoint"])
        if runtime.settings.environment == "production" and (
            not authorization_endpoint.startswith("https://")
            or not token_endpoint.startswith("https://")
        ):
            raise ValueError("insecure endpoint")
        return {
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint,
        }
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise NeuroError(
            "oidc_discovery_failed", "Hosted sign-in is temporarily unavailable.", 503
        ) from exc


@router.get("/session/oidc/login")
def begin_oidc_session(
    request: Request,
    return_to: str = "/workspace",
    runtime: Runtime = Depends(get_runtime),
) -> RedirectResponse:
    if not return_to.startswith("/") or return_to.startswith("//"):
        raise NeuroError("invalid_return_path", "Choose a valid application return path.", 422)
    client_id = runtime.settings.oidc_client_id
    if not client_id:
        raise NeuroError("oidc_not_configured", "Hosted sign-in is not configured.", 503)
    configuration = _oidc_configuration(runtime)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    redirect_uri = f"{runtime.settings.api_public_url.rstrip('/')}/api/session/oidc/callback"
    cookie = runtime.login_state_signer.dumps(
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "return_to": return_to,
        }
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(
        f"{configuration['authorization_endpoint']}?{query}", status_code=302
    )
    response.set_cookie(
        "nw_oidc_state",
        cookie,
        httponly=True,
        secure=runtime.settings.environment in {"staging", "production"},
        samesite="lax",
        max_age=600,
        path="/api/session/oidc",
    )
    return response


@router.get("/session/oidc/callback")
def complete_oidc_session(
    request: Request,
    code: str,
    state: str,
    runtime: Runtime = Depends(get_runtime),
    session: Session = Depends(get_db),
) -> RedirectResponse:
    state_cookie = request.cookies.get("nw_oidc_state")
    if not state_cookie or runtime.oidc is None or not runtime.settings.oidc_client_id:
        raise NeuroError("invalid_login_state", "Start sign-in again to continue.", 401)
    login = runtime.login_state_signer.loads(state_cookie)
    if not secrets.compare_digest(str(login.get("state", "")), state):
        raise NeuroError("invalid_login_state", "Start sign-in again to continue.", 401)
    configuration = _oidc_configuration(runtime)
    redirect_uri = f"{runtime.settings.api_public_url.rstrip('/')}/api/session/oidc/callback"
    token_request = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": runtime.settings.oidc_client_id,
        "code_verifier": str(login["verifier"]),
    }
    if runtime.settings.oidc_client_secret:
        token_request["client_secret"] = runtime.settings.oidc_client_secret.get_secret_value()
    try:
        token_response = httpx.post(configuration["token_endpoint"], data=token_request, timeout=10)
        token_response.raise_for_status()
        id_token = str(token_response.json()["id_token"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise NeuroError(
            "oidc_exchange_failed", "Sign-in could not be completed. Try again.", 401
        ) from exc
    identity = runtime.oidc.verify(id_token, nonce=str(login["nonce"]))
    existing = session.scalar(select(User.id).where(User.external_subject == identity.subject))
    user = ensure_user(
        session,
        subject=identity.subject,
        email=identity.email,
        display_name=identity.display_name,
    )
    if existing is None:
        track_product_event(session, "signup_completed", actor_id=user.id)
    session.commit()
    target = f"{runtime.settings.web_public_url.rstrip('/')}{login['return_to']}"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie("nw_oidc_state", path="/api/session/oidc")
    response.set_cookie(
        runtime.settings.session_cookie_name,
        runtime.signer.dumps(identity),
        httponly=True,
        secure=runtime.settings.environment in {"staging", "production"},
        samesite="lax",
        max_age=runtime.settings.session_ttl_seconds,
        path="/",
    )
    return response


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "workspace"
    return f"{base[:80]}-{uuid.uuid4().hex[:8]}"


@router.post("/session/dev", response_model=SessionResponse)
def create_dev_session(
    body: DevSessionRequest,
    response: Response,
    runtime: Runtime = Depends(get_runtime),
    session: Session = Depends(get_db),
) -> SessionResponse:
    if not runtime.settings.dev_auth_enabled or runtime.settings.environment == "production":
        raise NeuroError("dev_auth_disabled", "Development sign-in is disabled.", 404)
    identity = Identity(
        subject=f"dev:{body.email.casefold()}",
        email=body.email.casefold(),
        display_name=body.display_name,
    )
    existing = session.scalar(select(User.id).where(User.external_subject == identity.subject))
    user = ensure_user(
        session,
        subject=identity.subject,
        email=identity.email,
        display_name=identity.display_name,
    )
    if existing is None:
        track_product_event(session, "signup_completed", actor_id=user.id)
    session.commit()
    response.set_cookie(
        runtime.settings.session_cookie_name,
        runtime.signer.dumps(identity),
        httponly=True,
        secure=runtime.settings.environment in {"staging", "production"},
        samesite="strict",
        max_age=runtime.settings.session_ttl_seconds,
        path="/",
    )
    return SessionResponse(user=UserResponse.model_validate(user))


@router.delete("/session", status_code=204)
def delete_session(response: Response, runtime: Runtime = Depends(get_runtime)) -> None:
    response.delete_cookie(runtime.settings.session_cookie_name, path="/")


@router.get("/session", response_model=SessionResponse)
def read_session(user: User = Depends(current_user)) -> SessionResponse:
    return SessionResponse(user=UserResponse.model_validate(user))


@router.get("/workspaces", response_model=list[WorkspaceResponse])
def list_workspaces(
    user: User = Depends(current_user), session: Session = Depends(get_db)
) -> list[WorkspaceResponse]:
    rows = session.execute(
        select(Workspace, Membership.role)
        .join(
            Membership,
            (Membership.workspace_id == Workspace.id) & (Membership.user_id == user.id),
        )
        .where(Membership.is_active.is_(True))
        .order_by(Workspace.created_at, Workspace.id)
    ).all()
    return [
        WorkspaceResponse(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            role=role,
            created_at=workspace.created_at,
        )
        for workspace, role in rows
    ]


@router.post("/workspaces", response_model=WorkspaceResponse, status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> WorkspaceResponse:
    workspace = Workspace(name=body.name.strip(), slug=_slug(body.name))
    session.add(workspace)
    session.flush()
    session.add(Membership(workspace_id=workspace.id, user_id=user.id, role=Role.OWNER))
    track_product_event(
        session,
        "workspace_created",
        actor_id=user.id,
        workspace_id=workspace.id,
    )
    session.commit()
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        role=Role.OWNER,
        created_at=workspace.created_at,
    )


@router.get("/workspaces/{workspace_id}/projects", response_model=list[ProjectResponse])
def list_projects(
    workspace_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> list[ProjectResponse]:
    require_access(session, user.id, workspace_id, Capability.READ)
    projects = session.scalars(
        select(Project)
        .where(Project.workspace_id == workspace_id)
        .order_by(Project.created_at, Project.id)
    ).all()
    return [ProjectResponse.model_validate(project) for project in projects]


@router.post("/workspaces/{workspace_id}/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    workspace_id: str,
    body: ProjectCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> ProjectResponse:
    require_access(session, user.id, workspace_id, Capability.CREATE)
    project = Project(
        workspace_id=workspace_id, name=body.name.strip(), description=body.description.strip()
    )
    session.add(project)
    session.flush()
    track_product_event(
        session,
        "project_created",
        actor_id=user.id,
        workspace_id=workspace_id,
    )
    session.commit()
    return ProjectResponse.model_validate(project)


def _membership_response(membership: Membership, member: User) -> MembershipResponse:
    return MembershipResponse(
        id=membership.id,
        workspace_id=membership.workspace_id,
        user=UserResponse.model_validate(member),
        role=membership.role,
        is_active=membership.is_active,
        created_at=membership.created_at,
    )


def _membership_and_user(
    session: Session, workspace_id: str, membership_id: str
) -> tuple[Membership, User]:
    row = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.id == membership_id, Membership.workspace_id == workspace_id)
    ).one_or_none()
    if row is None:
        raise NeuroError("not_found", "The requested resource was not found.", 404)
    return row[0], row[1]


def _guard_owner_change(
    session: Session,
    workspace_id: str,
    actor_role: Role,
    membership: Membership,
    next_role: Role | None,
) -> None:
    if (membership.role == Role.OWNER or next_role == Role.OWNER) and actor_role != Role.OWNER:
        raise NeuroError("not_found", "The requested resource was not found.", 404)
    if membership.role == Role.OWNER and next_role != Role.OWNER:
        owner_count = session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.role == Role.OWNER,
                Membership.is_active.is_(True),
            )
        )
        if int(owner_count or 0) <= 1:
            raise NeuroError(
                "last_owner", "Transfer ownership before removing the last owner.", 409
            )


@router.get("/workspaces/{workspace_id}/memberships", response_model=list[MembershipResponse])
def list_memberships(
    workspace_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> list[MembershipResponse]:
    require_access(session, user.id, workspace_id, Capability.READ)
    rows = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == workspace_id, Membership.is_active.is_(True))
        .order_by(Membership.created_at, Membership.id)
    ).all()
    return [_membership_response(membership, member) for membership, member in rows]


@router.put("/workspaces/{workspace_id}/memberships", response_model=MembershipResponse)
def upsert_membership(
    workspace_id: str,
    body: MembershipUpsert,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> MembershipResponse:
    access = require_access(session, user.id, workspace_id, Capability.MANAGE_MEMBERS)
    if body.role == Role.OWNER and access.role != Role.OWNER:
        raise NeuroError("not_found", "The requested resource was not found.", 404)
    member = session.scalar(select(User).where(User.email == body.email.casefold()))
    if member is None:
        raise NeuroError(
            "account_required",
            "That person must sign in once before they can be added to a workspace.",
            422,
        )
    membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == member.id
        )
    )
    if membership is None:
        membership = Membership(
            workspace_id=workspace_id, user_id=member.id, role=body.role, is_active=True
        )
        session.add(membership)
    else:
        _guard_owner_change(session, workspace_id, access.role, membership, body.role)
        membership.role = body.role
        membership.is_active = True
    session.flush()
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=user.id,
            action="membership.upserted",
            target_type="membership",
            target_id=membership.id,
            safe_fields={"role": body.role.value},
        )
    )
    session.commit()
    return _membership_response(membership, member)


@router.patch(
    "/workspaces/{workspace_id}/memberships/{membership_id}",
    response_model=MembershipResponse,
)
def update_membership(
    workspace_id: str,
    membership_id: str,
    body: MembershipRoleUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> MembershipResponse:
    access = require_access(session, user.id, workspace_id, Capability.MANAGE_MEMBERS)
    membership, member = _membership_and_user(session, workspace_id, membership_id)
    _guard_owner_change(session, workspace_id, access.role, membership, body.role)
    membership.role = body.role
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=user.id,
            action="membership.role_changed",
            target_type="membership",
            target_id=membership.id,
            safe_fields={"role": body.role.value},
        )
    )
    session.commit()
    return _membership_response(membership, member)


@router.delete("/workspaces/{workspace_id}/memberships/{membership_id}", status_code=204)
def delete_membership(
    workspace_id: str,
    membership_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> None:
    access = require_access(session, user.id, workspace_id, Capability.MANAGE_MEMBERS)
    membership, _ = _membership_and_user(session, workspace_id, membership_id)
    _guard_owner_change(session, workspace_id, access.role, membership, None)
    membership.is_active = False
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=user.id,
            action="membership.removed",
            target_type="membership",
            target_id=membership.id,
        )
    )
    session.commit()


@router.get("/workspaces/{workspace_id}/metrics", response_model=WorkspaceMetricsResponse)
def get_workspace_metrics(
    workspace_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_db),
) -> WorkspaceMetricsResponse:
    """Return coarse, non-sensitive product signals to workspace operators."""

    require_access(session, user.id, workspace_id, Capability.MANAGE_WORKSPACE)

    def count(
        model: type[Membership] | type[Recording] | type[Annotation] | type[Export],
        *where: ColumnElement[bool],
    ) -> int:
        statement = select(func.count()).select_from(model).where(*where)
        return int(session.execute(statement).scalar_one())

    error_rows = session.execute(
        select(Recording.format_id, func.count())
        .where(
            Recording.workspace_id == workspace_id,
            Recording.state == RecordingState.FAILED,
        )
        .group_by(Recording.format_id)
    ).all()
    audit_counts: dict[str, int] = {
        str(action): int(total)
        for action, total in session.execute(
            select(AuditEvent.action, func.count())
            .where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action.in_(
                    [
                        "viewer.opened",
                        "membership.upserted",
                        "membership.role_changed",
                        "membership.removed",
                    ]
                ),
            )
            .group_by(AuditEvent.action)
        ).all()
    }
    return WorkspaceMetricsResponse(
        members=count(
            Membership,
            Membership.workspace_id == workspace_id,
            Membership.is_active.is_(True),
        ),
        recordings_uploaded=count(Recording, Recording.workspace_id == workspace_id),
        recordings_ready=count(
            Recording,
            Recording.workspace_id == workspace_id,
            Recording.state == RecordingState.READY,
        ),
        recordings_failed=count(
            Recording,
            Recording.workspace_id == workspace_id,
            Recording.state == RecordingState.FAILED,
        ),
        annotations_created=count(
            Annotation,
            Annotation.recording_id.in_(
                select(Recording.id).where(Recording.workspace_id == workspace_id)
            ),
        ),
        exports_created=count(
            Export,
            Export.recording_id.in_(
                select(Recording.id).where(Recording.workspace_id == workspace_id)
            ),
        ),
        viewer_opens=int(audit_counts.get("viewer.opened", 0)),
        membership_changes=sum(
            audit_counts.get(action, 0)
            for action in (
                "membership.upserted",
                "membership.role_changed",
                "membership.removed",
            )
        ),
        ingestion_errors_by_format={str(key or "unknown"): int(value) for key, value in error_rows},
    )
