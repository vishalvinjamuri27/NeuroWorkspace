.DEFAULT_GOAL := help

.PHONY: help install dev up down migrate seed lint format typecheck test test-python test-web test-e2e build openapi real-data coverage benchmark clean

help:
	@awk 'BEGIN {FS = ":.*## "; print "NeuroWorkspace tasks:"} /^[a-zA-Z_-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install locked Python and frontend dependencies
	uv sync --extra dev
	pnpm install --frozen-lockfile

dev: ## Run API and web locally (use separate terminals for worker)
	@echo "Run: uv run uvicorn neuro_api.main:app --reload --port 8000"
	@echo "Run: pnpm dev"
	@echo "Run: uv run celery -A neuro_worker.celery_app:celery_app worker --loglevel=INFO"

up: ## Start the full local stack
	docker compose up --build

down: ## Stop local services without deleting volumes
	docker compose down

migrate: ## Apply database migrations
	uv run alembic upgrade head

seed: ## Create the public sample workspace/recording
	uv run python scripts/seed_sample.py

lint: ## Run Python and frontend linters
	uv run ruff format --check .
	uv run ruff check .
	pnpm lint

format: ## Format maintained source files
	uv run ruff format .
	uv run ruff check --fix .
	pnpm exec prettier --write "apps/web/**/*.{ts,tsx,css,json}" "packages/shared-types/**/*.{ts,json}"

typecheck: ## Run Python and TypeScript type checks
	uv run mypy packages/signal-core/src apps/api/src apps/worker/src
	pnpm typecheck

test: test-python test-web ## Run fast automated tests

test-python: ## Run Python unit/integration tests except external real data
	uv run pytest tests/python -m "not real_data"

test-web: ## Run frontend tests
	pnpm test

test-e2e: ## Run browser smoke tests
	pnpm test:e2e

build: ## Build all deployable components
	uv build
	pnpm build

openapi: ## Regenerate checked-in OpenAPI and frontend types
	uv run python scripts/export_openapi.py
	pnpm --filter @neuroworkspace/shared-types generate

real-data: ## Fetch and run public real-data acceptance tests
	uv run python scripts/fetch_test_data.py fetch --sample sleep_edf_sc4001
	uv run python scripts/fetch_test_data.py fetch --sample public_biosemi_bdf
	uv run python scripts/fetch_test_data.py fetch --sample openneuro_ds004362_eeglab
	uv run python scripts/fetch_test_data.py fetch --sample openneuro_ds006547_brainvision
	uv run python scripts/fetch_test_data.py fetch --sample mne_official_fif
	uv run pytest tests/python -m real_data --real-data-root .cache/neuro-workspace/testdata

coverage: ## Run the complete pinned corpus and enforce the coverage floor
	uv run pytest tests/python --real-data-root .cache/neuro-workspace/testdata --cov --cov-report=term-missing

benchmark: ## Measure the generated 32-channel, 30-minute stress scenario
	uv run python scripts/benchmark_signal_store.py

clean: ## Remove only generated build and test artifacts
	uv run python scripts/clean_generated.py
