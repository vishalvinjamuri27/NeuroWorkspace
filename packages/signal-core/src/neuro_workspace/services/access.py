"""Workspace-scoped database access helpers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from neuro_workspace.db.models import Job, Membership, Project, Recording, User, Workspace
from neuro_workspace.domain import Capability, Role
from neuro_workspace.errors import ForbiddenError


@dataclass(frozen=True, slots=True)
class Access:
    workspace_id: str
    role: Role


def ensure_user(session: Session, *, subject: str, email: str, display_name: str) -> User:
    user = session.scalar(select(User).where(User.external_subject == subject))
    if user is None:
        user = User(external_subject=subject, email=email.casefold(), display_name=display_name)
        session.add(user)
        session.flush()
    return user


def membership_access(session: Session, user_id: str, workspace_id: str) -> Access:
    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id == workspace_id,
            Membership.is_active.is_(True),
        )
    )
    if membership is None:
        raise ForbiddenError()
    return Access(workspace_id, membership.role)


def require_access(
    session: Session, user_id: str, workspace_id: str, capability: Capability
) -> Access:
    from neuro_workspace.auth import require_capability

    access = membership_access(session, user_id, workspace_id)
    require_capability(access.role, capability)
    return access


def project_for_user(
    session: Session, user_id: str, project_id: str, capability: Capability = Capability.READ
) -> Project:
    project = session.scalar(
        select(Project)
        .join(Membership, Membership.workspace_id == Project.workspace_id)
        .where(
            Project.id == project_id,
            Membership.user_id == user_id,
            Membership.is_active.is_(True),
        )
    )
    if project is None:
        raise ForbiddenError()
    require_access(session, user_id, project.workspace_id, capability)
    return project


def recording_for_user(
    session: Session, user_id: str, recording_id: str, capability: Capability = Capability.READ
) -> Recording:
    recording = session.scalar(
        select(Recording)
        .join(Membership, Membership.workspace_id == Recording.workspace_id)
        .where(
            Recording.id == recording_id,
            Recording.deleted_at.is_(None),
            Membership.user_id == user_id,
            Membership.is_active.is_(True),
        )
    )
    if recording is None:
        raise ForbiddenError()
    require_access(session, user_id, recording.workspace_id, capability)
    return recording


def job_for_user(
    session: Session, user_id: str, job_id: str, capability: Capability = Capability.READ
) -> Job:
    job = session.scalar(
        select(Job)
        .join(Membership, Membership.workspace_id == Job.workspace_id)
        .where(Job.id == job_id, Membership.user_id == user_id, Membership.is_active.is_(True))
    )
    if job is None:
        raise ForbiddenError()
    require_access(session, user_id, job.workspace_id, capability)
    return job


def workspace_query_for_user(user_id: str) -> Select[tuple[Workspace]]:
    return (
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id, Membership.is_active.is_(True))
        .order_by(Workspace.created_at, Workspace.id)
    )
