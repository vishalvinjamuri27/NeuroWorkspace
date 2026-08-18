"""Authentication, workspace, and project routes."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from neuro_api.runtime import Runtime, current_user, get_db, get_runtime
from neuro_api.schemas import (
    DevSessionRequest,
    ProjectCreate,
    ProjectResponse,
    SessionResponse,
    UserResponse,
    WorkspaceCreate,
    WorkspaceResponse,
)
from neuro_workspace.auth import Identity
from neuro_workspace.db.models import Membership, Project, User, Workspace
from neuro_workspace.domain import Capability, Role
from neuro_workspace.errors import NeuroError
from neuro_workspace.services.access import require_access

router = APIRouter()


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
    from neuro_workspace.services.access import ensure_user

    user = ensure_user(
        session,
        subject=identity.subject,
        email=identity.email,
        display_name=identity.display_name,
    )
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
    session.commit()
    return ProjectResponse.model_validate(project)
