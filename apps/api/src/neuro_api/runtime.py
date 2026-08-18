"""Request dependencies and initialized process resources."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import cast

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from neuro_workspace.auth import Identity, LoginStateSigner, OIDCVerifier, SessionSigner
from neuro_workspace.config import Settings
from neuro_workspace.db.models import User
from neuro_workspace.errors import NeuroError
from neuro_workspace.services.access import ensure_user
from neuro_workspace.signal_store import LocalZarrSignalStore
from neuro_workspace.storage import ObjectStore


@dataclass(slots=True)
class Runtime:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    object_store: ObjectStore
    signal_store: LocalZarrSignalStore
    signer: SessionSigner
    login_state_signer: LoginStateSigner
    oidc: OIDCVerifier | None


def get_runtime(request: Request) -> Runtime:
    return cast(Runtime, request.app.state.runtime)


def get_db(runtime: Runtime = Depends(get_runtime)) -> Generator[Session, None, None]:
    session = runtime.session_factory()
    try:
        yield session
    finally:
        session.close()


def optional_identity(
    request: Request,
    runtime: Runtime = Depends(get_runtime),
    authorization: str | None = Header(default=None),
    session_cookie: str | None = Cookie(default=None, alias="nw_session"),
) -> Identity | None:
    cookie = request.cookies.get(runtime.settings.session_cookie_name, session_cookie)
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token or runtime.oidc is None:
            raise NeuroError("invalid_token", "Sign in again to continue.", 401)
        return runtime.oidc.verify(token)
    if cookie:
        return runtime.signer.loads(cookie)
    return None


def current_identity(identity: Identity | None = Depends(optional_identity)) -> Identity:
    if identity is None:
        raise NeuroError("authentication_required", "Sign in to continue.", 401)
    return identity


def current_user(
    identity: Identity = Depends(current_identity), session: Session = Depends(get_db)
) -> User:
    user = ensure_user(
        session,
        subject=identity.subject,
        email=identity.email,
        display_name=identity.display_name,
    )
    session.commit()
    return user
