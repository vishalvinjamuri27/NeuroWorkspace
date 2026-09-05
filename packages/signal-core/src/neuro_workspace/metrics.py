"""Prometheus metrics: what to instrument was chosen from the failure modes
and hot paths this project actually has, not "everything measurable".

HTTP: request count/latency/errors, because that is what an operator pages
on first. Jobs: started/succeeded/failed/retried and lease-reclaim counts,
because those are exactly the events the outbox dispatcher and lease
mechanism (services.jobs) exist to make visible -- a stuck queue or a
worker that keeps losing its lease should show up here before a user
reports it. Cache: hit/miss/error, to make the waveform/QC cache-aside
layer's actual effectiveness (or a Redis outage silently degrading it to
0% hit rate) observable rather than assumed.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS_TOTAL = Counter(
    "neuroworkspace_http_requests_total",
    "HTTP requests by route template, method, and status class.",
    ["method", "route", "status"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "neuroworkspace_http_request_duration_seconds",
    "HTTP request duration by route template and method.",
    ["method", "route"],
)

JOBS_STARTED_TOTAL = Counter(
    "neuroworkspace_jobs_started_total", "Jobs successfully claimed by a worker.", ["kind"]
)
JOBS_SUCCEEDED_TOTAL = Counter(
    "neuroworkspace_jobs_succeeded_total", "Jobs that reached SUCCEEDED.", ["kind"]
)
JOBS_FAILED_TOTAL = Counter(
    "neuroworkspace_jobs_failed_total", "Jobs that reached FAILED.", ["kind"]
)
JOB_LEASES_RECLAIMED_TOTAL = Counter(
    "neuroworkspace_job_leases_reclaimed_total",
    "Jobs reset to QUEUED after their lease expired (a crashed/killed worker).",
)
JOBS_DEAD_LETTERED_TOTAL = Counter(
    "neuroworkspace_jobs_dead_lettered_total",
    "Jobs that exhausted max_attempts and were not retried further.",
)
JOBS_REDISPATCHED_TOTAL = Counter(
    "neuroworkspace_jobs_redispatched_total",
    "Jobs redriven to the broker by the outbox sweep (not the synchronous dispatch path).",
)

CACHE_REQUESTS_TOTAL = Counter(
    "neuroworkspace_cache_requests_total",
    "Cache-aside lookups by cache and outcome (hit/miss/error).",
    ["cache", "outcome"],
)


async def metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    # request.scope["route"].path is the path *template*
    # ("/recordings/{recording_id}"), not the interpolated URL -- using the
    # template keeps cardinality bounded regardless of how many distinct
    # recording ids get requested.
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    status_class = f"{response.status_code // 100}xx"
    HTTP_REQUESTS_TOTAL.labels(method=request.method, route=route_path, status=status_class).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, route=route_path).observe(duration)
    return response


def render_metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
