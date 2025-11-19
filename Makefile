.PHONY: help install dev-install up down logs clean test lint format type-check build

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	uv sync

dev-install: ## Install development dependencies
	uv sync --dev

up: ## Start all services with docker compose
	docker compose up -d

down: ## Stop all services
	docker compose down

logs: ## Follow docker compose logs
	docker compose logs -f

logs-api: ## Follow API logs
	docker compose logs -f api

logs-worker: ## Follow worker logs
	docker compose logs -f worker

logs-temporal: ## Follow Temporal logs
	docker compose logs -f temporal

clean: ## Stop services and remove volumes
	docker compose down -v
	rm -rf logs/

build: ## Build docker images
	docker compose build

restart: ## Restart all services
	docker compose restart

restart-api: ## Restart API service
	docker compose restart api

restart-worker: ## Restart worker service
	docker compose restart worker

ps: ## Show service status
	docker compose ps

shell-api: ## Open shell in API container
	docker compose exec api /bin/bash

shell-worker: ## Open shell in worker container
	docker compose exec worker /bin/bash

shell-db: ## Open PostgreSQL shell
	docker compose exec postgresql psql -U temporal -d voice_ai

test: ## Run tests
	uv run pytest

test-cov: ## Run tests with coverage
	uv run pytest --cov=src --cov-report=html --cov-report=term

lint: ## Run linter
	uv run ruff check src tests

format: ## Format code
	uv run black src tests
	uv run ruff check --fix src tests

type-check: ## Run type checking
	uv run mypy src

dev-api: ## Run API locally (requires infrastructure services running)
	uv run uvicorn src.voice_ai_system.api.main:app --reload --host 0.0.0.0 --port 8000

dev-worker: ## Run worker locally (requires infrastructure services running)
	uv run python -m src.voice_ai_system.worker

infra-up: ## Start only infrastructure services (PostgreSQL, Temporal, etc.)
	docker compose up -d postgresql temporal temporal-ui elasticsearch prometheus grafana

infra-down: ## Stop infrastructure services
	docker compose stop postgresql temporal temporal-ui elasticsearch prometheus grafana
