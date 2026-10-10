#!/usr/bin/env bash
# ==============================================================================
# Multi-tenant cron wrapper with per-tenant overlap protection (flock)
# Usage: ./scripts/run-tenant-cron.sh <tenant-id> [extra rss-morning args...]
# ==============================================================================
set -euo pipefail

TENANT_ID="${1:-}"
if [[ -z "${TENANT_ID}" ]]; then
    echo "ERROR: Missing tenant-id." >&2
    echo "Usage: $0 <tenant-id> [extra args...]" >&2
    exit 2
fi
shift

# Default lock directory; can be overridden via RSS_LOCK_DIR
LOCK_DIR="${RSS_LOCK_DIR:-/tmp/rss_morning_locks}"
mkdir -p "${LOCK_DIR}"
LOCK_FILE="${LOCK_DIR}/tenant_${TENANT_ID}.lock"

# Determine invocation mode:
# If RSS_USE_DOCKER=1, invoke through docker compose.
# Otherwise invoke python directly.
if [[ "${RSS_USE_DOCKER:-0}" == "1" ]]; then
    PROJECT_DIR="${RSS_PROJECT_DIR:-/home/sgzmd/code/docker-configs/rss-new}"
    CMD=(docker compose -f "${PROJECT_DIR}/docker-compose.yml" --project-directory "${PROJECT_DIR}" run --rm rss-morning --tenant "${TENANT_ID}" "$@")
else
    PYTHON_BIN="${PYTHON_BIN:-python3}"
    CMD=("${PYTHON_BIN}" -m rss_morning.cli --tenant "${TENANT_ID}" "$@")
fi

# Acquire non-blocking per-tenant lock if flock is available
if command -v flock >/dev/null 2>&1; then
    exec 200>"${LOCK_FILE}"
    if ! flock -n 200; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] Tenant '${TENANT_ID}' is already running. Skipping overlapping execution." >&2
        exit 0
    fi
fi

# Execute tenant run in a dedicated, isolated process
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Launching briefing for tenant '${TENANT_ID}'..."
exec "${CMD[@]}"
