"""Celery worker configuration and idempotent task wrappers."""

from __future__ import annotations

from celery import Celery

from neuro_workspace.adapters.registry import default_registry
from neuro_workspace.config import get_settings
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.services.jobs import (
    execute_deletion_job,
    execute_ingestion_job,
    execute_processing_job,
)
from neuro_workspace.signal_store import LocalZarrSignalStore
from neuro_workspace.storage import create_object_store

settings = get_settings()
engine, factory = create_engine_and_factory(settings.database_url)
object_store = create_object_store(settings)
signal_store = LocalZarrSignalStore(settings.object_store_root / "canonical")

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
    },
)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="neuroworkspace.ingest", autoretry_for=(OSError,), retry_backoff=True, max_retries=3
)
def ingest(job_id: str) -> None:
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
    execute_processing_job(
        factory=factory, signal_store=signal_store, settings=settings, job_id=job_id
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="neuroworkspace.delete", autoretry_for=(OSError,), retry_backoff=True, max_retries=5
)
def delete(job_id: str) -> None:
    execute_deletion_job(
        factory=factory,
        object_store=object_store,
        signal_store=signal_store,
        job_id=job_id,
    )
