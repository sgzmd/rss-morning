# ==============================================================================
# Makefile for RSS Morning (rss-new) deployment to 'max' server
# ==============================================================================

REMOTE_HOST     ?= max
DOCKER_CONTEXT  ?= max
REMOTE_DIR      ?= /home/sgzmd/code/docker-configs/docker/rss-new
IMAGE_NAME      ?= rss-morning-new
GIT_REV         := $(shell git rev-parse --short HEAD 2>/dev/null || echo latest)

.PHONY: all help build config secrets feeds dry-run deploy test

all: help

help:
	@echo "Available targets:"
	@echo "  make build    - Build Docker image on '$(REMOTE_HOST)' using Docker context '$(DOCKER_CONTEXT)'"
	@echo "  make config   - Copy config files (prompts, config.toml) to $(REMOTE_HOST):$(REMOTE_DIR)/configs/"
	@echo "  make feeds    - Copy feeds.xml to $(REMOTE_HOST):$(REMOTE_DIR)/configs/"
	@echo "  make secrets  - Securely copy local .env to $(REMOTE_HOST):$(REMOTE_DIR)/configs/.env"
	@echo "  make deploy   - Copy configs and build Docker image on $(REMOTE_HOST)"
	@echo "  make dry-run  - Execute dry-run test container on $(REMOTE_HOST)"
	@echo "  make test     - Run local pytest test suite"

# ------------------------------------------------------------------------------
# Build Docker image directly on remote daemon via Docker context
# ------------------------------------------------------------------------------
build:
	@echo "==> Building Docker image '$(IMAGE_NAME):latest' on context '$(DOCKER_CONTEXT)' (commit: $(GIT_REV))..."
	docker --context $(DOCKER_CONTEXT) build \
		-t $(IMAGE_NAME):latest \
		-t $(IMAGE_NAME):$(GIT_REV) \
		.
	@echo "==> Build complete on $(REMOTE_HOST)."

# ------------------------------------------------------------------------------
# Sync configuration directory (config.toml, prompt files, templates)
# ------------------------------------------------------------------------------
config:
	@echo "==> Ensuring remote configs directory exists at $(REMOTE_HOST):$(REMOTE_DIR)/configs/..."
	ssh $(REMOTE_HOST) "mkdir -p $(REMOTE_DIR)/configs"
	@echo "==> Syncing configs/ (including config.toml and prompt files)..."
	rsync -avz --exclude='.env' configs/ $(REMOTE_HOST):$(REMOTE_DIR)/configs/
	@echo "==> Configs synchronized successfully."

# ------------------------------------------------------------------------------
# Optional sync helpers for feeds and environment secrets
# ------------------------------------------------------------------------------
feeds:
	@echo "==> Copying feeds.xml to $(REMOTE_HOST):$(REMOTE_DIR)/configs/feeds.xml..."
	scp feeds.xml $(REMOTE_HOST):$(REMOTE_DIR)/configs/feeds.xml

secrets:
	@echo "==> Securely transferring .env secrets to $(REMOTE_HOST):$(REMOTE_DIR)/configs/.env..."
	scp .env $(REMOTE_HOST):$(REMOTE_DIR)/configs/.env
	ssh $(REMOTE_HOST) "chmod 0600 $(REMOTE_DIR)/configs/.env"

# ------------------------------------------------------------------------------
# Full deploy: sync configs and build remote image
# ------------------------------------------------------------------------------
deploy: config build
	@echo "==> Deployment of configs and image to $(REMOTE_HOST) completed."

# ------------------------------------------------------------------------------
# Verification dry-run on max
# ------------------------------------------------------------------------------
dry-run:
	@echo "==> Executing remote dry-run container on $(REMOTE_HOST)..."
	ssh $(REMOTE_HOST) "docker compose -f $(REMOTE_DIR)/docker-compose.yml --project-directory $(REMOTE_DIR) run --rm rss-morning --config /app/configs/config.toml --llm-dry-run"

dry-run-tenant:
	@echo "==> Executing remote dry-run container for tenant '$(TENANT)' on $(REMOTE_HOST)..."
	ssh $(REMOTE_HOST) "docker compose -f $(REMOTE_DIR)/docker-compose.yml --project-directory $(REMOTE_DIR) run --rm rss-morning --tenant $(TENANT) --llm-dry-run"

# ------------------------------------------------------------------------------
# Local unit tests
# ------------------------------------------------------------------------------
test:
	./venv/bin/pytest -v
