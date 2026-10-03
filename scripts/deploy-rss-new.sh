#!/usr/bin/env bash
# ==============================================================================
# Hardened Deployment Script for RSS Morning (rss-new)
# Target: Remote host 'max' via Docker context and SSH
# Detailed timestamped logging enabled with local audit trail.
# ==============================================================================
set -euo pipefail

# Ensure local logs directory exists
LOCAL_LOG_DIR="logs"
mkdir -p "${LOCAL_LOG_DIR}"
DEPLOY_LOG="${LOCAL_LOG_DIR}/deploy_$(date +%Y%m%d_%H%M%S).log"

# Configuration
REMOTE_HOST="max"
REMOTE_BASE_DIR="/home/sgzmd/code/docker-configs"
REMOTE_DOCKER_DIR="${REMOTE_BASE_DIR}/docker/rss-new"
REMOTE_SYMLINK="${REMOTE_BASE_DIR}/rss-new"
EXISTING_RSS_DIR="${REMOTE_BASE_DIR}/docker/rss"
LOCAL_ENV_FILE=".env"
LOCAL_FEEDS_FILE="feeds.xml"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

ts() { date +"%Y-%m-%d %H:%M:%S"; }

log_info() {
    local msg="[$(ts)] [INFO] $1"
    echo -e "${BLUE}${msg}${NC}"
    echo "${msg}" >> "${DEPLOY_LOG}"
}

log_success() {
    local msg="[$(ts)] [SUCCESS] $1"
    echo -e "${GREEN}${msg}${NC}"
    echo "${msg}" >> "${DEPLOY_LOG}"
}

log_warn() {
    local msg="[$(ts)] [WARN] $1"
    echo -e "${YELLOW}${msg}${NC}"
    echo "${msg}" >> "${DEPLOY_LOG}"
}

log_error() {
    local msg="[$(ts)] [ERROR] $1"
    echo -e "${RED}${msg}${NC}" >&2
    echo "${msg}" >> "${DEPLOY_LOG}"
}

log_step() {
    local header="[$(ts)] =========================================================================="
    local step_title="[$(ts)] >>> STEP $1: $2"
    echo -e "\n${CYAN}${header}\n${step_title}\n${header}${NC}"
    {
        echo ""
        echo "${header}"
        echo "${step_title}"
        echo "${header}"
    } >> "${DEPLOY_LOG}"
}

log_detail() {
    echo "    $1" | tee -a "${DEPLOY_LOG}"
}

log_info "Deployment log initiated at: ${DEPLOY_LOG}"

# ------------------------------------------------------------------------------
# STEP 1: Pre-flight checks and diagnostics
# ------------------------------------------------------------------------------
log_step 1 "Running pre-flight checks and gathering environment diagnostics"

# Check required local binaries
for cmd in docker ssh git sha256sum date; do
    if command -v "$cmd" >/dev/null 2>&1; then
        log_detail "Binary available: $cmd ($(command -v "$cmd"))"
    elif [[ "$cmd" == "sha256sum" ]] && command -v shasum >/dev/null 2>&1; then
        log_detail "Binary available: shasum (using for sha256)"
    else
        log_error "Required local command '$cmd' not found."
        exit 1
    fi
done

# Git repository state diagnostics
GIT_REV=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
GIT_COMMIT_MSG=$(git log -1 --pretty=%s 2>/dev/null || echo "no commit message")
GIT_DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')

log_info "Local Git Repository Diagnostics:"
log_detail "Branch: ${GIT_BRANCH}"
log_detail "Commit: ${GIT_REV}"
log_detail "Subject: ${GIT_COMMIT_MSG}"
log_detail "Uncommitted modified files: ${GIT_DIRTY}"

# Check required local files
if [[ ! -f "${LOCAL_ENV_FILE}" ]]; then
    log_error "Local .env file (${LOCAL_ENV_FILE}) not found. Secrets must be present locally."
    exit 1
fi
log_detail "Found local env file: ${LOCAL_ENV_FILE} ($(wc -c < "${LOCAL_ENV_FILE}" | tr -d ' ') bytes)"

if [[ ! -f "${LOCAL_FEEDS_FILE}" ]]; then
    log_error "Local feeds file (${LOCAL_FEEDS_FILE}) not found."
    exit 1
fi
FEEDS_COUNT=$(grep -c "<outline" "${LOCAL_FEEDS_FILE}" || echo "0")
log_detail "Found local feeds file: ${LOCAL_FEEDS_FILE} (${FEEDS_COUNT} outlines, $(wc -c < "${LOCAL_FEEDS_FILE}" | tr -d ' ') bytes)"

# Verify required application secrets exist in local .env
log_info "Verifying required credentials in ${LOCAL_ENV_FILE} (safely masked):"
for key in TYPESAFE_API_KEY OPENROUTER_API_KEY RESEND_API_KEY; do
    if ! grep -q "^${key}=" "${LOCAL_ENV_FILE}"; then
        log_error "Missing required secret '${key}' in ${LOCAL_ENV_FILE}."
        exit 1
    fi
    val_len=$(grep "^${key}=" "${LOCAL_ENV_FILE}" | cut -d'=' -f2- | tr -d '\r\n"' | wc -c | tr -d ' ')
    prefix=$(grep "^${key}=" "${LOCAL_ENV_FILE}" | cut -d'=' -f2- | tr -d '\r\n"' | cut -c1-6)
    suffix=$(grep "^${key}=" "${LOCAL_ENV_FILE}" | cut -d'=' -f2- | tr -d '\r\n"' | tail -c 5 | tr -d '\n')
    log_detail "${key}: present (length: ${val_len} chars, format: ${prefix}...${suffix})"
done

# Docker context diagnostics
CURRENT_CONTEXT=$(docker context show)
log_info "Checking Docker context (current: '${CURRENT_CONTEXT}')..."
if [[ "${CURRENT_CONTEXT}" != "${REMOTE_HOST}" ]]; then
    log_warn "Docker context is currently '${CURRENT_CONTEXT}'. Switching to '${REMOTE_HOST}'..."
    docker context use "${REMOTE_HOST}" >> "${DEPLOY_LOG}" 2>&1
fi
log_success "Active Docker context verified: $(docker context show)"

# Remote SSH connectivity and host diagnostics
log_info "Connecting to ${REMOTE_HOST} to inspect host environment..."
SSH_CHECK=$(ssh -q -o BatchMode=yes -o ConnectTimeout=5 "${REMOTE_HOST}" "uname -srm && df -h /home | tail -n 1" || true)
if [[ -z "${SSH_CHECK}" ]]; then
    log_error "Cannot connect to SSH host '${REMOTE_HOST}'."
    exit 1
fi
log_success "SSH connection to ${REMOTE_HOST} confirmed:"
while IFS= read -r line; do
    log_detail "$line"
done <<< "${SSH_CHECK}"

IMAGE_TAG="rss-morning-new:${GIT_REV}"
LATEST_TAG="rss-morning-new:latest"

# ------------------------------------------------------------------------------
# STEP 2: Build hardened Docker image remotely via Docker context
# ------------------------------------------------------------------------------
log_step 2 "Building hardened Docker image on ${REMOTE_HOST} (${IMAGE_TAG} and ${LATEST_TAG})"

BUILD_START=$(date +%s)
log_info "Starting remote docker build (sending build context over SSH docker context)..."
docker build \
    --tag "${IMAGE_TAG}" \
    --tag "${LATEST_TAG}" \
    . 2>&1 | tee -a "${DEPLOY_LOG}"

BUILD_END=$(date +%s)
BUILD_DURATION=$((BUILD_END - BUILD_START))
log_success "Docker image successfully built in ${BUILD_DURATION} seconds."

# Inspect the built image on remote host
log_info "Inspecting newly built image metadata on ${REMOTE_HOST}:"
IMAGE_INFO=$(docker image inspect "${IMAGE_TAG}" --format 'ID: {{.Id}} | Size: {{.Size}} bytes | Arch: {{.Architecture}} | User: {{.Config.User}}' 2>/dev/null || echo "Unable to inspect")
log_detail "${IMAGE_INFO}"

# ------------------------------------------------------------------------------
# STEP 3: Create remote directory structure on max
# ------------------------------------------------------------------------------
log_step 3 "Creating remote directory structure at ${REMOTE_DOCKER_DIR}"

log_info "Setting up remote directories with strict permissions..."
ssh "${REMOTE_HOST}" "bash -s" << 'EOF' | tee -a "${DEPLOY_LOG}"
set -euo pipefail
REMOTE_BASE_DIR="/home/sgzmd/code/docker-configs"
REMOTE_DOCKER_DIR="${REMOTE_BASE_DIR}/docker/rss-new"
REMOTE_SYMLINK="${REMOTE_BASE_DIR}/rss-new"

mkdir -p "${REMOTE_DOCKER_DIR}/configs"
mkdir -p "${REMOTE_DOCKER_DIR}/logs"
chmod 0700 "${REMOTE_DOCKER_DIR}/logs"
# Pre-initialize .env with restrictive permissions so compose validation succeeds
touch "${REMOTE_DOCKER_DIR}/configs/.env"
chmod 0600 "${REMOTE_DOCKER_DIR}/configs/.env"

# Use ln -sfn for atomic and idempotent symlink creation
ln -sfn "docker/rss-new" "${REMOTE_SYMLINK}"
echo "Remote directories created: ${REMOTE_DOCKER_DIR}"
echo "Symlink configured: ${REMOTE_SYMLINK} -> $(readlink "${REMOTE_SYMLINK}")"
EOF
log_success "Remote directory structure and symlink verified."

# ------------------------------------------------------------------------------
# STEP 4: Install remote docker-compose.yml
# ------------------------------------------------------------------------------
log_step 4 "Installing hardened docker-compose.yml"

log_info "Writing ${REMOTE_DOCKER_DIR}/docker-compose.yml..."
ssh "${REMOTE_HOST}" "cat > '${REMOTE_DOCKER_DIR}/docker-compose.yml'" << 'EOF'
services:
  rss-morning:
    image: rss-morning-new:latest
    restart: "no"
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    command:
      - --config
      - /app/configs/config.toml
      - --log-level
      - "INFO"
    volumes:
      - ${RSS_PROJECT_DIR:-/home/sgzmd/code/docker-configs/rss-new}/configs:/app/configs:ro
    env_file:
      - ${RSS_PROJECT_DIR:-/home/sgzmd/code/docker-configs/rss-new}/configs/.env
EOF

# Validate remote compose file syntax
log_info "Validating remote docker compose configuration syntax..."
ssh "${REMOTE_HOST}" "docker compose -f '${REMOTE_DOCKER_DIR}/docker-compose.yml' --project-directory '${REMOTE_DOCKER_DIR}' config --quiet" \
    && log_success "Remote docker-compose.yml syntax validated successfully."

# ------------------------------------------------------------------------------
# STEP 5: Install remote start.sh wrapper script
# ------------------------------------------------------------------------------
log_step 5 "Installing hardened start.sh wrapper script on ${REMOTE_HOST}"

log_info "Writing ${REMOTE_DOCKER_DIR}/start.sh..."
ssh "${REMOTE_HOST}" "cat > '${REMOTE_DOCKER_DIR}/start.sh' && chmod 0755 '${REMOTE_DOCKER_DIR}/start.sh'" << 'EOF'
#!/bin/bash
set -euo pipefail
umask 077

PROJECT_DIR="${RSS_PROJECT_DIR:-/home/sgzmd/code/docker-configs/rss-new}"
MONITORING_ENV="${RSS_MONITORING_ENV:-$PROJECT_DIR/monitoring.env}"

if [[ -r "$MONITORING_ENV" ]]; then
    set -a
    source "$MONITORING_ENV"
    set +a
fi

PAPERTRAIL_HOST="${PAPERTRAIL_HOST:-https://logs.collector.eu-01.cloud.solarwinds.com}"
LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="$LOG_DIR/run_$(date +%F).log"
mkdir -p "$LOG_DIR"
chmod 0700 "$LOG_DIR"

log_ts() { date +"%Y-%m-%d %H:%M:%S"; }

echo "--------------------------------------------------" >> "$LOG_FILE"
echo "[$(log_ts)] Starting RSS Morning job at $(date)" >> "$LOG_FILE"
echo "[$(log_ts)] Project directory: $PROJECT_DIR" >> "$LOG_FILE"

# Log to Papertrail safely using curl config file to avoid leaking Bearer token in process list
log_event() {
    [[ -n "${PAPERTRAIL_TOKEN:-}" ]] || return 0
    curl --silent --show-error --location --request POST \
         "${PAPERTRAIL_HOST}/v1/logs" \
         --header "Content-Type: application/octet-stream" \
         --config <(printf 'header = "Authorization: Bearer %s"\n' "${PAPERTRAIL_TOKEN}") \
         --data-raw "$1" || true
}

upload_logs() {
    [[ -n "${PAPERTRAIL_TOKEN:-}" ]] || return 0
    curl --silent --show-error --location --request POST \
         "${PAPERTRAIL_HOST}/v1/logs/bulk" \
         --header "Content-Type: application/octet-stream" \
         --config <(printf 'header = "Authorization: Bearer %s"\n' "${PAPERTRAIL_TOKEN}") \
         --data-binary "@$1" || true
}

log_event "Starting RSS (rss-new) job on $(hostname)"

RID=""
if [[ -n "${HC_UUID:-}" ]]; then
    RID="$(uuidgen)"
    echo "[$(log_ts)] Notifying Healthchecks start (RID: $RID)" >> "$LOG_FILE"
    curl -fsS -m 10 --retry 5 \
         "https://hc-ping.com/$HC_UUID/start?rid=$RID" || true
fi

JOB_START=$(date +%s)
set +e
docker compose -f "$PROJECT_DIR/docker-compose.yml" \
    --project-directory "$PROJECT_DIR" run --rm rss-morning 2>&1 \
    | tee -a "$LOG_FILE"
EXIT_CODE=$?
set -e
JOB_END=$(date +%s)
JOB_DURATION=$((JOB_END - JOB_START))

echo "[$(log_ts)] Container execution finished in ${JOB_DURATION}s with exit code ${EXIT_CODE}" >> "$LOG_FILE"

if [[ -n "${HC_UUID:-}" ]]; then
    tail -c 100000 "$LOG_FILE" | curl -fsS -m 10 --retry 5 -X POST \
         -H "Content-Type: text/plain" \
         --data-binary @- \
         "https://hc-ping.com/$HC_UUID/log?rid=$RID" || true
    curl -fsS -m 10 --retry 5 \
         "https://hc-ping.com/$HC_UUID/$EXIT_CODE" || true
fi

upload_logs "$LOG_FILE"
log_event "Finished RSS (rss-new) job with exit code $EXIT_CODE (duration: ${JOB_DURATION}s)"

if [[ $EXIT_CODE -eq 0 ]]; then
    echo "[$(log_ts)] SUCCESS: Job completed at $(date)" >> "$LOG_FILE"
else
    echo "[$(log_ts)] FAILURE: Job crashed with exit code $EXIT_CODE at $(date)" >> "$LOG_FILE"
    echo "!!! RSS JOB FAILED (exit code: $EXIT_CODE) !!!" >&2
    tail -n 20 "$LOG_FILE" >&2
fi

exit "$EXIT_CODE"
EOF
log_success "Hardened start.sh installed and chmod 0755."

# ------------------------------------------------------------------------------
# STEP 6: Securely provision monitoring.env on remote host
# ------------------------------------------------------------------------------
log_step 6 "Synchronizing monitoring.env securely on ${REMOTE_HOST}"

log_info "Copying existing monitoring configuration safely on ${REMOTE_HOST}..."
ssh "${REMOTE_HOST}" "bash -s" << 'EOF' | tee -a "${DEPLOY_LOG}"
set -euo pipefail
REMOTE_BASE_DIR="/home/sgzmd/code/docker-configs"
REMOTE_DOCKER_DIR="${REMOTE_BASE_DIR}/docker/rss-new"
EXISTING_MONITORING="${REMOTE_BASE_DIR}/docker/rss/monitoring.env"

if [[ -f "${EXISTING_MONITORING}" ]]; then
    install -m 0600 "${EXISTING_MONITORING}" "${REMOTE_DOCKER_DIR}/monitoring.env"
    echo "Synchronized monitoring.env from ${EXISTING_MONITORING} (mode 0600)"
else
    echo "# Monitoring configuration" > "${REMOTE_DOCKER_DIR}/monitoring.env"
    chmod 0600 "${REMOTE_DOCKER_DIR}/monitoring.env"
    echo "Initialized empty monitoring.env (mode 0600)"
fi
ls -la "${REMOTE_DOCKER_DIR}/monitoring.env"
EOF
log_success "monitoring.env safely provisioned."

# ------------------------------------------------------------------------------
# STEP 7: Install config.toml
# ------------------------------------------------------------------------------
log_step 7 "Installing configs/config.toml on ${REMOTE_HOST}"

log_info "Writing ${REMOTE_DOCKER_DIR}/configs/config.toml..."
ssh "${REMOTE_HOST}" "cat > '${REMOTE_DOCKER_DIR}/configs/config.toml'" << 'EOF'
feeds = "feeds.xml"
limit = 50
max_age_hours = 24.0
max_article_length = 250
summary = true
concurrency = 10

[classification]
enabled = true
model = "jev-latest"
threshold = 0.50

[email]
to = "sigizmund@gmail.com"
from = "mailer@r-k.co"
subject = "Morning RSS Digest"

[logging]
level = "INFO"

[llm]
model = "google/gemini-3.8-flash"

[areas.mobile_security]
label = "Mobile Security"
threshold = 0.40
description = "Mobile application security, Android and iOS vulnerabilities, mobile malware, device attestation, and client integrity."

[areas.corporate_security]
label = "Corporate Security"
threshold = 0.45
description = "Enterprise networking, VPN, SD-WAN, firewalls, identity providers, managed devices, browsers, and workplace IT infrastructure."

[areas.account_takeover]
label = "Account Takeover"
threshold = 0.40
description = "Account takeover, credential stuffing, session hijacking, password spraying, brute force, and customer identity compromise."

[areas.end_user_security]
label = "End-User Security (Passkeys & Modern Auth)"
threshold = 0.40
description = "Passkeys, FIDO2, WebAuthn, modern authentication methods, biometric login, MFA adoption, and end-user identity protection."

[areas.ai_security]
label = "AI Security"
threshold = 0.50
description = "AI model vulnerabilities, prompt injection, AI agent safety, and security implications of LLMs and generative AI."

[areas.other]
label = "Everything Else & Notable CVEs"
threshold = 0.75
description = "General security news, notable CVEs, software vulnerabilities, infrastructure, or material not fitting any specific focus area above."
EOF

CONFIG_CHECKSUM=$(ssh "${REMOTE_HOST}" "sha256sum '${REMOTE_DOCKER_DIR}/configs/config.toml' | cut -d' ' -f1")
log_success "config.toml installed (remote SHA256: ${CONFIG_CHECKSUM})."

# ------------------------------------------------------------------------------
# STEP 8: Copy feeds.xml from local dev machine
# ------------------------------------------------------------------------------
log_step 8 "Copying ${LOCAL_FEEDS_FILE} to ${REMOTE_HOST}"

LOCAL_FEEDS_SHA=$(shasum -a 256 "${LOCAL_FEEDS_FILE}" | cut -d' ' -f1)
log_info "Streaming ${LOCAL_FEEDS_FILE} to ${REMOTE_DOCKER_DIR}/configs/feeds.xml..."
cat "${LOCAL_FEEDS_FILE}" | ssh "${REMOTE_HOST}" "cat > '${REMOTE_DOCKER_DIR}/configs/feeds.xml'"

REMOTE_FEEDS_SHA=$(ssh "${REMOTE_HOST}" "sha256sum '${REMOTE_DOCKER_DIR}/configs/feeds.xml' | cut -d' ' -f1")
if [[ "${LOCAL_FEEDS_SHA}" == "${REMOTE_FEEDS_SHA}" ]]; then
    log_success "feeds.xml checksum verified (${LOCAL_FEEDS_SHA})."
else
    log_error "feeds.xml checksum mismatch! Local: ${LOCAL_FEEDS_SHA} vs Remote: ${REMOTE_FEEDS_SHA}"
    exit 1
fi

# ------------------------------------------------------------------------------
# STEP 9: Securely transfer environment secrets from local dev machine
# ------------------------------------------------------------------------------
log_step 9 "Securely transferring environment secrets from local ${LOCAL_ENV_FILE} to ${REMOTE_HOST}"

log_info "Piping secrets to ${REMOTE_DOCKER_DIR}/configs/.env with umask 077..."
cat "${LOCAL_ENV_FILE}" | ssh "${REMOTE_HOST}" "umask 077 && cat > '${REMOTE_DOCKER_DIR}/configs/.env' && chmod 0600 '${REMOTE_DOCKER_DIR}/configs/.env'"

REMOTE_ENV_PERMS=$(ssh "${REMOTE_HOST}" "stat -c '%a %U:%G' '${REMOTE_DOCKER_DIR}/configs/.env'")
REMOTE_ENV_LINES=$(ssh "${REMOTE_HOST}" "wc -l < '${REMOTE_DOCKER_DIR}/configs/.env' | tr -d ' '")
log_success "Environment secrets securely installed (mode: ${REMOTE_ENV_PERMS}, lines: ${REMOTE_ENV_LINES})."

# ------------------------------------------------------------------------------
# STEP 10: Remote verification smoke test (Dry Run)
# ------------------------------------------------------------------------------
log_step 10 "Executing remote container dry-run verification"

log_info "Launching dry-run container on ${REMOTE_HOST}..."
SMOKE_START=$(date +%s)

ssh "${REMOTE_HOST}" \
    "docker compose -f '${REMOTE_DOCKER_DIR}/docker-compose.yml' --project-directory '${REMOTE_DOCKER_DIR}' run --rm rss-morning --config /app/configs/config.toml --llm-dry-run" \
    2>&1 | tee -a "${DEPLOY_LOG}"

SMOKE_END=$(date +%s)
SMOKE_DURATION=$((SMOKE_END - SMOKE_START))
log_success "Dry run verification completed successfully in ${SMOKE_DURATION} seconds!"

# Final status & cutover guidance
log_step "FINAL" "Deployment Summary"
echo ""
echo "=========================================================================="
echo "Deployment of rss-new to ${REMOTE_HOST} is complete."
echo "Location: ${REMOTE_DOCKER_DIR} (symlink: ${REMOTE_SYMLINK})"
echo "Audit Log: ${DEPLOY_LOG}"
echo ""
echo "Existing production rss remains untouched at: ${EXISTING_RSS_DIR}"
echo ""
echo "To switch cron to use rss-new, update crontab on ${REMOTE_HOST}:"
echo "  crontab -e"
echo "  # Replace existing cron with:"
echo "  0 20 * * * /home/sgzmd/code/docker-configs/rss-new/start.sh"
echo "=========================================================================="
