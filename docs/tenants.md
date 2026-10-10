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

- **`config.toml`** (Required): Primary tenant configuration specifying feeds, classification criteria, areas, email delivery, and LLM parameters.
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
   Specifying both `--tenant` and `--config` simultaneously is disallowed and will produce a command-line argument error.

## Cross-Tenant Path Isolation & Security

To prevent cross-tenant data contamination, information leakage, and unauthorized access to private context (such as portfolio holdings or credentials):

1. **Path Resolution:** All relative paths within a tenant's `config.toml` (`feeds`, `prompt_file`, `grounding_rules_file`, `holdings_file`, `env_file`, `logging.file`) resolve relative to that tenant's directory.
2. **Boundary Enforcement:** Any resolved path that points into the `configs/tenants/` hierarchy MUST reside strictly inside that tenant's own directory (`configs/tenants/<tenant-id>/`).
3. **Traversal Prevention:** Attempting to reference another tenant's directory (e.g. `prompt_file = "../other-tenant/prompt.md"`) or using symlinks targeting another tenant's files is detected and rejected with an access violation error.
4. **Shared Project Assets:** Paths referencing shared global assets outside `configs/tenants/` (such as a shared feed file in the project root) are permitted.

## Version Control & Privacy Boundaries

Live tenant directories and sensitive context are kept private:

- Tracked examples reside in `configs/tenants/example-*` (e.g., `configs/tenants/example-security/`, `configs/tenants/example-markets/`).
- Actual tenant directories (e.g. `configs/tenants/prod-finance/`) and live holdings files (`holdings.toml`, `holdings.json`) are ignored by Git via `.gitignore`.
- Example files (`*.example.toml`, `*.toml.example`) are tracked for reference and documentation.
- Tenant private credentials, holdings, and full prompts are masked in routine logs and dry-run outputs.
