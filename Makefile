VENV ?= .venv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
ENV_FISH ?= env.fish
CONFIG ?= configs/config.xml

.PHONY: help test live-e2e production

help:
	@echo "Available targets:"
	@echo "  make test        Run the hermetic test suite"
	@echo "  make live-e2e    Run the live pipeline with detailed console logging"
	@echo "  make production  Run the full production pipeline"

test:
	@test -x "$(PYTEST)" || { echo "Missing $(PYTEST). Create the virtual environment first." >&2; exit 2; }
	@$(PYTEST) -m "not live_e2e"

live-e2e:
	@test -x "$(PYTEST)" || { echo "Missing $(PYTEST). Create the virtual environment first." >&2; exit 2; }
	@command -v fish >/dev/null || { echo "The fish shell is required to load $(ENV_FISH)." >&2; exit 2; }
	@test -f "$(ENV_FISH)" || { echo "Missing $(ENV_FISH). Add OPENROUTER_API_KEY there first." >&2; exit 2; }
	@fish -c 'source "$(ENV_FISH)"; or exit 2; set -q OPENROUTER_API_KEY; or begin; echo "OPENROUTER_API_KEY is not set by $(ENV_FISH)." >&2; exit 2; end; set -lx RUN_LIVE_E2E 1; "$(PYTEST)" -m live_e2e -vv -s --tb=long'

production:
	@test -x "$(PYTHON)" || { echo "Missing $(PYTHON). Create the virtual environment first." >&2; exit 2; }
	@test -f "$(CONFIG)" || { echo "Missing $(CONFIG). Copy configs/config.xml.example and configure it first." >&2; exit 2; }
	@$(PYTHON) main.py --config "$(CONFIG)"
