VENV ?= .venv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
ENV_FISH ?= env.fish
CONFIG ?= configs/config.xml

.PHONY: help test coverage check lock container-smoke live-e2e llm-eval production

help:
	@echo "Available targets:"
	@echo "  make test        Run the hermetic test suite"
	@echo "  make coverage    Run hermetic tests with 100% branch coverage"
	@echo "  make check       Run lint, format, type, test, and coverage checks"
	@echo "  make lock        Regenerate pinned runtime and development dependencies"
	@echo "  make container-smoke  Build and verify the offline production image"
	@echo "  make live-e2e    Run the live pipeline with detailed console logging"
	@echo "  make llm-eval    Run explicitly approved paid model-quality evaluation"
	@echo "  make production  Run the full production pipeline"

test:
	@test -x "$(PYTEST)" || { echo "Missing $(PYTEST). Create the virtual environment first." >&2; exit 2; }
	@$(PYTEST) -m "not live_e2e"

coverage:
	@test -x "$(PYTEST)" || { echo "Missing $(PYTEST). Create the virtual environment first." >&2; exit 2; }
	@$(PYTEST) -m "not live_e2e" --cov=rss_morning --cov=main --cov-branch --cov-report=term-missing --cov-fail-under=100

check:
	@test -x "$(VENV)/bin/ruff" || { echo "Missing development tools. Install requirements-dev.txt first." >&2; exit 2; }
	@$(VENV)/bin/ruff check .
	@$(VENV)/bin/ruff format --check .
	@$(VENV)/bin/mypy rss_morning main.py
	@$(MAKE) coverage

lock:
	@CUSTOM_COMPILE_COMMAND='make lock' $(VENV)/bin/pip-compile --resolver=backtracking --strip-extras --output-file=requirements.txt requirements.in
	@CUSTOM_COMPILE_COMMAND='make lock' $(VENV)/bin/pip-compile --resolver=backtracking --strip-extras --output-file=requirements-dev.txt requirements-dev.in

container-smoke:
	@docker build --tag rss-morning:smoke .
	@sh scripts/container-smoke.sh rss-morning:smoke

live-e2e:
	@test -x "$(PYTEST)" || { echo "Missing $(PYTEST). Create the virtual environment first." >&2; exit 2; }
	@command -v fish >/dev/null || { echo "The fish shell is required to load $(ENV_FISH)." >&2; exit 2; }
	@test -f "$(ENV_FISH)" || { echo "Missing $(ENV_FISH). Add OPENROUTER_API_KEY there first." >&2; exit 2; }
	@fish -c 'source "$(ENV_FISH)"; or exit 2; set -q OPENROUTER_API_KEY; or begin; echo "OPENROUTER_API_KEY is not set by $(ENV_FISH)." >&2; exit 2; end; set -lx RUN_LIVE_E2E 1; "$(PYTEST)" -m live_e2e -vv -s --tb=long'

llm-eval:
	@test "$(RUN_PAID_LLM_EVAL)" = "1" || { echo "Set RUN_PAID_LLM_EVAL=1 to approve paid evaluation." >&2; exit 2; }
	@test -n "$(PROMPT)" || { echo "Set PROMPT to a synthetic evaluation prompt path." >&2; exit 2; }
	@RUN_PAID_LLM_EVAL=1 $(PYTHON) -m rss_morning.summary_eval --prompt "$(PROMPT)"

production:
	@test -x "$(PYTHON)" || { echo "Missing $(PYTHON). Create the virtual environment first." >&2; exit 2; }
	@test -f "$(CONFIG)" || { echo "Missing $(CONFIG). Copy configs/config.xml.example and configure it first." >&2; exit 2; }
	@$(PYTHON) main.py --config "$(CONFIG)"
