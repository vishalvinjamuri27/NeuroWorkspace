"""FastAPI application factory."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from neuro_api.routes_product import router as product_router
from neuro_api.routes_recordings import router as recordings_router
from neuro_api.routes_session import router as session_router
from neuro_api.routes_uploads import router as uploads_router
from neuro_api.runtime import Runtime
from neuro_workspace.auth import LoginStateSigner, OIDCVerifier, SessionSigner
from neuro_workspace.config import Settings, get_settings
from neuro_workspace.db.models import Base, Recording
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.domain import RecordingState
from neuro_workspace.errors import NeuroError
from neuro_workspace.signal_store import LocalZarrSignalStore
from neuro_workspace.storage import create_object_store


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    selected = settings or get_settings()
    _configure_logging(selected)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine, factory = create_engine_and_factory(selected.database_url)
        if selected.environment == "test" or selected.database_url.startswith("sqlite"):
            Base.metadata.create_all(engine)
        object_store = create_object_store(selected)
        signal_store = LocalZarrSignalStore(selected.object_store_root / "canonical")
        oidc = (
            OIDCVerifier(selected.oidc_issuer, selected.oidc_audience)
            if selected.oidc_issuer and selected.oidc_audience
            else None
        )
        app.state.runtime = Runtime(
            settings=selected,
            engine=engine,
            session_factory=factory,
            object_store=object_store,
            signal_store=signal_store,
            signer=SessionSigner(
                selected.session_secret.get_secret_value(), selected.session_ttl_seconds
            ),
            login_state_signer=LoginStateSigner(selected.session_secret.get_secret_value()),
            oidc=oidc,
        )
        yield
        engine.dispose()

    app = FastAPI(
        title="NeuroWorkspace API",
        version="0.1.0-alpha.1",
        description=(
            "Non-clinical research/engineering API for private neural and "
            "physiological recordings. "
            "QC outputs are review heuristics, not diagnostic conclusions."
        ),
        default_response_class=JSONResponse,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=selected.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key", "If-Match"],
        expose_headers=["ETag", "Retry-After", "X-Correlation-ID"],
    )

    @app.middleware("http")
    async def request_boundary(request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))[:64]
        unsafe = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        origin = request.headers.get("origin")
        if unsafe and origin and origin not in selected.allowed_origins:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "origin_forbidden",
                        "message": "The request origin is not allowed.",
                        "correlation_id": correlation_id,
                        "details": {},
                    }
                },
            )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(NeuroError)
    async def handle_neuro_error(request: Request, exc: NeuroError) -> JSONResponse:
        correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))[:64]
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "correlation_id": correlation_id,
                    "details": dict(exc.details),
                }
            },
            headers={"X-Correlation-ID": correlation_id},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))[:64]
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()[:20]]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Review the highlighted request fields and try again.",
                    "correlation_id": correlation_id,
                    "details": {"fields": fields},
                }
            },
            headers={"X-Correlation-ID": correlation_id},
        )

    @app.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", include_in_schema=False)
    def ready(request: Request) -> dict[str, str]:
        runtime: Runtime = request.app.state.runtime
        with runtime.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        if runtime.settings.require_demo_ready:
            with runtime.session_factory() as session:
                demo = session.scalar(
                    select(Recording)
                    .where(
                        Recording.is_demo.is_(True),
                        Recording.state == RecordingState.READY,
                        Recording.deleted_at.is_(None),
                    )
                    .order_by(Recording.created_at)
                )
                if (
                    demo is None
                    or demo.active_version_id is None
                    or not runtime.signal_store.version_path(demo.active_version_id).exists()
                ):
                    raise NeuroError(
                        "demo_not_ready",
                        "The deployment sample has not passed its readiness check.",
                        503,
                    )
        return {"status": "ready", "build": runtime.settings.build_version}

    app.include_router(session_router, prefix="/api", tags=["identity and workspaces"])
    app.include_router(uploads_router, prefix="/api", tags=["uploads"])
    app.include_router(recordings_router, prefix="/api", tags=["recordings"])
    app.include_router(product_router, prefix="/api", tags=["product evidence"])
    return app


app = create_app()
