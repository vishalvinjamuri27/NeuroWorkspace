FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.14.7-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=uv /uv /bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY packages/signal-core packages/signal-core
COPY apps/api apps/api
COPY apps/worker apps/worker
COPY alembic.ini ./
COPY alembic alembic
COPY scripts/fetch_test_data.py scripts/fetch_test_data.py
COPY scripts/seed_public_sample.py scripts/seed_public_sample.py
COPY scripts/seed_sample.py scripts/seed_sample.py
COPY testdata/manifest.yaml testdata/manifest.yaml
RUN uv sync --frozen --no-dev --no-cache && \
    addgroup --system neuroworkspace && \
    adduser --system --ingroup neuroworkspace --home /app neuroworkspace && \
    mkdir -p /data/objects/canonical && \
    chown -R neuroworkspace:neuroworkspace /app /data
# Numeric, not the name: Kubernetes' `securityContext.runAsNonRoot` check
# inspects the image's declared USER and cannot verify a *named* user is
# non-root (found by actually scheduling this image on a real cluster --
# kubelet refused to start the container: "cannot verify user is
# non-root"). `adduser --system` assigned uid 100 / gid 101 here; keep
# this in sync if that ever changes (`docker run --rm <image> id`).
USER 100:101
EXPOSE 8000
CMD ["uvicorn", "neuro_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
