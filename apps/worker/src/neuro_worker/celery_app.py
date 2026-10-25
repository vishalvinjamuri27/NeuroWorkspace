"""Celery worker configuration and idempotent task wrappers."""

from __future__ import annotations

import os

from celery import Celery
from celery.signals import worker_process_init

from neuro_workspace.adapters.registry import default_registry
from neuro_workspace.config import get_settings
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.services.jobs import (
    execute_deletion_job,
    execute_ingestion_job,
    execute_processing_job,
    run_dispatch_sweep,
)
from neuro_workspace.signal_store import ZarrSignalStore, create_signal_store
from neuro_workspace.storage import ObjectStore, create_object_store

settings = get_settings()

# NOT constructed here: engine/object_store/signal_store must not be built
# in the parent process. Celery's default pool is prefork -- it forks a
# child process per worker slot AFTER this module has already been
# imported once by the parent. s3fs/fsspec's S3ZarrSignalStore lazily binds
# an asyncio event loop to whatever OS thread first touches it; a forked
# child only inherits the thread that called fork(), not that loop's
# thread, so fsspec deliberately raises `RuntimeError("This class is not
# fork-safe")` the first time a child tries to use an S3ZarrSignalStore
# built in the parent. This was not a theoretical concern -- it broke the
# very first real ingestion run against a live worker pool. Building these
# per-process, after fork, via worker_process_init is the standard fix
# (paralleling SQLAlchemy's own documented advice to dispose() an Engine
# created before a fork).
engine = None
factory = None
object_store: ObjectStore | None = None
signal_store: ZarrSignalStore | None = None
_initialized_pid: int | None = None


@worker_process_init.connect  # type: ignore[untyped-decorator]
def _init_worker_process(**_kwargs: object) -> None:
    global engine, factory, object_store, signal_store, _initialized_pid
    engine, factory = create_engine_and_factory(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
    )
    object_store = create_object_store(settings)
    signal_store = create_signal_store(settings)
    _initialized_pid = os.getpid()


def _ensure_worker_resources() -> None:
    """Build this process's clients on first use if nothing has yet.

    `--pool=solo` and other non-forking paths never fire worker_process_init.
    Keying on the PID also means a forked child can never reuse clients built
    in its parent, even if the signal were missed.
    """

    if _initialized_pid != os.getpid():
        _init_worker_process()


celery_app = Celery("neuroworkspace", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    task_routes={
        "neuroworkspace.ingest": {"queue": "ingestion"},
        "neuroworkspace.process": {"queue": "processing"},
        "neuroworkspace.delete": {"queue": "maintenance"},
        "neuroworkspace.reclaim_jobs": {"queue": "maintenance"},
    },
    # The reclaim sweep is the outbox dispatcher and the crash-recovery path
    # in one: it redrives jobs the API committed but never confirmed sending
    # to the broker, and it resets jobs whose worker lease expired (a crashed
    # or killed worker) back to QUEUED so another worker can claim them.
    beat_schedule={
        "reclaim-stale-jobs": {
            "task": "neuroworkspace.reclaim_jobs",
            "schedule": 30.0,
        },
    },
)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="neuroworkspace.ingest", autoretry_for=(OSError,), retry_backoff=True, max_retries=3
)
def ingest(job_id: str) -> None:
    _ensure_worker_resources()
    assert factory is not None and object_store is not None and signal_store is not None
    execute_ingestion_job(
        factory=factory,
        object_store=object_store,
        signal_store=signal_store,
        registry=default_registry,
        settings=settings,
        job_id=job_id,
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="neuroworkspace.process", autoretry_for=(OSError,), retry_backoff=True, max_retries=3
)
def process(job_id: str) -> None:
    _ensure_worker_resources()
    assert factory is not None and signal_store is not None
    execute_processing_job(
        factory=factory, signal_store=signal_store, settings=settings, job_id=job_id
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="neuroworkspace.delete", autoretry_for=(OSError,), retry_backoff=True, max_retries=5
)
def delete(job_id: str) -> None:
    _ensure_worker_resources()
    assert factory is not None and object_store is not None and signal_store is not None
    execute_deletion_job(
        factory=factory,
        object_store=object_store,
        signal_store=signal_store,
        job_id=job_id,
    )


_REDISPATCH_ROUTES = {
    "INGESTION": ("neuroworkspace.ingest", "ingestion"),
    "PROCESSING": ("neuroworkspace.process", "processing"),
    "DELETION": ("neuroworkspace.delete", "maintenance"),
}


def _redispatch(job_id: str, kind: str) -> None:
    route = _REDISPATCH_ROUTES.get(kind)
    if route is None:
        return
    task_name, queue = route
    celery_app.send_task(task_name, args=[job_id], queue=queue)


@celery_app.task(name="neuroworkspace.reclaim_jobs")  # type: ignore[untyped-decorator]
def reclaim_jobs() -> dict[str, int]:
    """Periodic outbox dispatcher + crash-recovery sweep. See services.jobs."""

    _ensure_worker_resources()
    assert factory is not None
    return run_dispatch_sweep(factory=factory, redispatch=_redispatch)
