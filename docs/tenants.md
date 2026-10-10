# Multi-Tenant Architecture & Conventions

## Overview

RSS Morning supports multiple isolated tenants through file-based configuration. Each tenant represents an independent briefing profile with its own feeds, classification taxonomy, editorial prompts, grounding rules, delivery settings, and optional portfolio holdings context.

## Tenant Directory Convention

Live tenant configurations are stored under:

```text
configs/tenants/<tenant-id>/
```

### Directory Contents

Each tenant directory contains its dedicated configuration and content assets:

- **`config.toml`** (Required): Primary tenant configuration specifying feeds, classification criteria, areas, email delivery, profile, and LLM parameters.
- **`prompt.md`** (Configured via `prompt_file`): Editorial policy, tone instructions, and priority coverage directives for the tenant.
- **`grounding_rules.md`** (Optional, configured via `grounding_rules_file`): Domain-specific factual verification rules.
- **`feeds.xml`** (Configured via `feeds`): Tenant-specific OPML feed subscriptions.
- **`holdings.toml`** (Optional, configured via `holdings_file`): Structured, read-only portfolio asset context with an `as_of` date.
- **`.env`** (Optional, configured via `env_file`): Tenant-specific environment variables.

### Tenant Identifier Constraints

Tenant IDs (`<tenant-id>`) must adhere to:
- 1 to 64 characters in length.
- Start with an alphanumeric character (`[a-zA-Z0-9]`).
- Contain only letters, numbers, hyphens (`-`), or underscores (`_`).
- Path traversal sequences (`..`), slashes (`/`, `\`), and dots (`.`) are strictly disallowed.

## Command-Line Invocation & Compatibility Rules

RSS Morning preserves complete backward compatibility with existing single-config workflows while providing explicit multi-tenant support:

1. **Explicit Tenant Selection:**
   ```bash
   python main.py --tenant <tenant-id>
   ```
   Resolves and executes `configs/tenants/<tenant-id>/config.toml`.

2. **Legacy Config Path:**
   ```bash
   python main.py --config configs/config.toml
   ```
   Executes the specified configuration file directly.

3. **Default Invocation:**
   ```bash
   python main.py
   ```
   Defaults to `configs/config.toml` if neither `--tenant` nor `--config` is supplied.

4. **Mutual Exclusivity:**
   Specifying both `--tenant` and `--config` simultaneously is disallowed and produces a command-line argument error.

## Cross-Tenant Path Isolation & Security

To prevent cross-tenant data contamination, information leakage, and unauthorized access to private context (such as portfolio holdings or credentials):

1. **Path Resolution:** All relative paths within a tenant's `config.toml` (`feeds`, `prompt_file`, `grounding_rules_file`, `holdings_file`, `env_file`, `logging.file`) resolve relative to that tenant's directory.
2. **Boundary Enforcement:** Any resolved path that points into the `configs/tenants/` hierarchy MUST reside strictly inside that tenant's own directory (`configs/tenants/<tenant-id>/`).
3. **Traversal Prevention:** Attempting to reference another tenant's directory (e.g. `prompt_file = "../other-tenant/prompt.md"`) or using symlinks targeting another tenant's files is detected and rejected with an access violation error.
4. **Shared Project Assets:** Paths referencing shared global assets outside `configs/tenants/` (such as a shared feed file in the project root) are permitted.

## Private Configuration Migration Guide

To migrate an existing single-tenant deployment (`configs/config.toml`) into the multi-tenant architecture:

1. Choose a stable identifier for the existing briefing (e.g. `primary-security`).
2. Create the directory:
   ```bash
   mkdir -p configs/tenants/primary-security
   ```
3. Move or copy the existing private assets into the tenant directory:
   ```bash
   cp configs/config.toml configs/tenants/primary-security/config.toml
   cp configs/prompt.md configs/tenants/primary-security/prompt.md
   cp feeds.xml configs/tenants/primary-security/feeds.xml
   ```
4. Verify relative paths inside `configs/tenants/primary-security/config.toml`:
   - `feeds = "feeds.xml"`
   - `prompt_file = "prompt.md"`
5. Validate via dry-run without sending email:
   ```bash
   python main.py --tenant primary-security --llm-dry-run
   ```
6. Update deployment crontab to invoke the tenant via `--tenant primary-security` or the wrapper script `scripts/run-tenant-cron.sh`.

## How to Add a New Tenant

To provision a new tenant (e.g. `global-markets`):

1. **Create the directory:**
   ```bash
   mkdir -p configs/tenants/global-markets
   ```
2. **Create `config.toml`:**
   Configure `tenant_id`, `profile` (`"security"` or `"markets"`), `title`, `subtitle`, `timezone`, and email delivery.
   ```toml
   tenant_id = "global-markets"
   profile = "markets"
   title = "Global Markets Briefing"
   subtitle = "Institutional Market Intelligence"
   timezone = "America/New_York"
   feeds = "feeds.xml"
   prompt_file = "prompt.md"
   summary = true

   [email]
   to = "markets-team@example.com"
   from = "digest@example.com"
   subject = "Morning Markets Intelligence"
   ```
3. **Add feeds and prompt:**
   Create curated `feeds.xml` (OPML format) and `prompt.md` with editorial policy in `configs/tenants/global-markets/`.
4. **(Optional) Add grounding rules:**
   Create `grounding_rules.md` and set `grounding_rules_file = "grounding_rules.md"` in `config.toml`.
5. **(Optional) Supply holdings:**
   Create `holdings.toml` and set `holdings_file = "holdings.toml"` in `config.toml` (see below).
6. **Test offline:**
   ```bash
   python main.py --tenant global-markets --llm-dry-run
   ```

## Supplying Portfolio Holdings Context

Tenants may optionally supply structured, read-only portfolio holdings context.

### Invariants & Rules

- **Explicit File Required:** Holdings are loaded **only** from a configured `holdings_file` (`holdings.toml` or `holdings.json`).
- **Watchlists are NOT Holdings:** Configured `[technologies]` (e.g. watched asset classes or stock lists) represent editorial watchlist themes and are **never** treated as owned positions.
- **Strict Grounding:** Holdings data is treated as context for relevance calibration and conditional impact assessment. It is **never** evidence for market prices, yields, returns, or corporate actions without supporting source articles.
- **Fail Fast:** Missing or malformed holdings files (e.g. missing `as_of` date or missing `holdings` list) immediately fail execution with a descriptive error.
- **Privacy & Leakage Prevention:** Holdings data, private account identifiers, and tenant credentials are never logged in routine logs, dry-run output, or shared with other tenants.

### Holdings File Format (`holdings.toml`)

```toml
as_of = "2026-03-31"

[[holdings]]
asset = "Apple Inc."
ticker = "AAPL"
asset_class = "Equities"
currency = "USD"
weight_pct = 5.2
notes = "Core long position"

[[holdings]]
asset = "US 10-Year Treasury Note"
ticker = "UST10Y"
asset_class = "Fixed Income"
currency = "USD"
weight_pct = 12.0
```

## Scheduling via Cron

Scheduling is maintained **exclusively outside the application** via standard host cron. Send times are **not** duplicated in tenant TOML files.

### Host Cron Timezone

The host cron runs in **UTC**. Because standard cron does not support per-entry `CRON_TZ` reliably across systems, all trigger times in crontab are specified in host UTC time. The `timezone` setting in tenant TOML controls date formatting in email subjects and bodies, independent of when cron runs.

### Independent Cron Entries & Overlap Protection

Each tenant has its own independent crontab entry and its own non-blocking file lock via `flock`:

```cron
# Tenant: Security Briefing (Preserving existing production schedule: 20:00 UTC)
0 20 * * * /usr/bin/flock -n /tmp/rss_sec.lock /path/to/scripts/run-tenant-cron.sh example-security >> /path/to/logs/sec.log 2>&1

# Tenant: Markets Briefing (11:00 UTC / 07:00 EDT Mon-Fri)
0 11 * * 1-5 /usr/bin/flock -n /tmp/rss_mkt.lock /path/to/scripts/run-tenant-cron.sh example-markets >> /path/to/logs/mkt.log 2>&1
```

### Process Isolation

Each cron execution runs **one tenant in one process**. Process environment variables, working directories, and Resend API keys never cross tenant boundaries.

## Validating Without Sending (Dry Run Guarantee)

Use `--llm-dry-run` to validate configurations, feeds, and LLM prompts without sending email:

```bash
python main.py --tenant <tenant-id> --llm-dry-run
```

- **Zero-Send Invariant:** When `--llm-dry-run` is passed, `send_email_report` is **never** invoked under any execution path (`summary = true` or `summary = false`).
- **Observable Failures:** If configuration or context is invalid, the command exits with non-zero status (`1` or `2`).
- **Safe Output:** Sensitive fields (`system_prompt`, `grounding_rules`, `holdings_file`) are masked in routine console logs.
