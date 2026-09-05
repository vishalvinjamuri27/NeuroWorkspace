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
from neuro_workspace.cache import ViewportCache
from neuro_workspace.config import Settings, get_settings
from neuro_workspace.db.models import Base, Recording
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.domain import RecordingState
from neuro_workspace.errors import NeuroError
from neuro_workspace.metrics import metrics_middleware, render_metrics
from neuro_workspace.signal_store import create_signal_store
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
        engine, factory = create_engine_and_factory(
            selected.database_url,
            pool_size=selected.db_pool_size,
            max_overflow=selected.db_max_overflow,
            pool_timeout=selected.db_pool_timeout_seconds,
        )
        if selected.environment == "test" or selected.database_url.startswith("sqlite"):
            Base.metadata.create_all(engine)
        object_store = create_object_store(selected)
        signal_store = create_signal_store(selected)
        cache = ViewportCache(selected.redis_url)
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
            cache=cache,
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

    @app.middleware("http")
    async def record_metrics(request: Request, call_next: RequestResponseEndpoint) -> Response:
        return await metrics_middleware(request, call_next)

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

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Last-resort handler.

        Found by actually killing MinIO under a live request: an unhandled
        `botocore.exceptions.EndpointConnectionError` from the storage layer
        (any dependency outage not already translated to a NeuroError can
        reach here the same way) was reaching Starlette's default handler,
        which returns a bare "Internal Server Error" text body -- breaking
        this API's own documented contract that every error is the
        {"error": {code, message, correlation_id, details}} envelope
        (docs/API.md), and handing the client an opaque, unparseable
        response instead of something it can branch on. The real exception
        is still logged server-side with a diagnostic_id for support/
        on-call to correlate, the same pattern services.jobs uses for job
        failures -- the client just never sees internals it cannot act on.
        """

        correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))[:64]
        diagnostic_id = str(uuid.uuid4())
        structlog.get_logger(__name__).error(
            "unhandled_exception",
            diagnostic_id=diagnostic_id,
            correlation_id=correlation_id,
            path=request.url.path,
            method=request.method,
            failure_class=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": (
                        "Something went wrong on our end. Retry, or contact support "
                        f"with diagnostic ID {diagnostic_id}."
                    ),
                    "correlation_id": correlation_id,
                    "details": {},
                }
            },
            headers={"X-Correlation-ID": correlation_id},
        )

    @app.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return render_metrics()

    @app.get("/health/ready", include_in_schema=False)
    def ready(request: Request) -> dict[str, str]:
        """Traffic gate for the orchestrator.

        Every dependency check here is wrapped so a downstream outage (DB,
        or object storage when checking the demo sample) always reports a
        clean 503 "not ready" -- found by actually killing MinIO under a
        live request: an unhandled botocore exception from
        signal_store.exists() was escaping this endpoint as a bare,
        unstructured 500 instead. An orchestrator polling this route only
        needs a reliable non-2xx signal to pull the replica from traffic;
        it must never get an opaque crash instead of "not ready" from the
        one endpoint whose entire job is to report exactly that.
        """

        runtime: Runtime = request.app.state.runtime
        try:
            with runtime.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise NeuroError("database_unavailable", "The database is unreachable.", 503) from exc
        if runtime.settings.require_demo_ready:
            try:
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
                    demo_ready = (
                        demo is not None
                        and demo.active_version_id is not None
                        and runtime.signal_store.exists(demo.active_version_id)
                    )
            except NeuroError:
                raise
            except Exception as exc:
                raise NeuroError(
                    "demo_check_unavailable",
                    "The deployment sample's readiness check could not be completed.",
                    503,
                ) from exc
            if not demo_ready:
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
