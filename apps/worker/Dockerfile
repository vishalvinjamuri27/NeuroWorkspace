FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.13.7-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=uv /uv /bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY packages/signal-core packages/signal-core
COPY apps/api apps/api
COPY apps/worker apps/worker
RUN uv sync --frozen --no-dev --no-cache && \
    addgroup --system neuroworkspace && \
    adduser --system --ingroup neuroworkspace --home /app neuroworkspace && \
    mkdir -p /data/objects && chown -R neuroworkspace:neuroworkspace /app /data
USER neuroworkspace
CMD ["celery", "-A", "neuro_worker.celery_app:celery_app", "worker", "--loglevel=INFO", "--queues=ingestion,processing,maintenance"]
