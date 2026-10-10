"""Multi-tenant directory resolution, ID validation, and path isolation."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

TENANT_ID_REGEX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
DEFAULT_TENANTS_DIR = Path("configs/tenants")


def validate_tenant_id(tenant_id: str) -> str:
    """Validate a stable tenant identifier.

    Rules:
    - Must be non-empty and stripped.
    - 1-64 characters starting with an alphanumeric character.
    - Allowed characters: alphanumeric, hyphen, underscore.
    - Directory traversal characters ('/', '\\', '..') are strictly disallowed.
    """
    if not isinstance(tenant_id, str):
        raise ValueError("Tenant ID must be a string.")

    cleaned = tenant_id.strip()
    if not cleaned:
        raise ValueError("Tenant ID cannot be empty.")

    if "/" in cleaned or "\\" in cleaned or ".." in cleaned or "." in cleaned:
        raise ValueError(
            f"Invalid tenant ID '{cleaned}': path traversal and dot characters are not permitted."
        )

    if not TENANT_ID_REGEX.match(cleaned):
        raise ValueError(
            f"Invalid tenant ID '{cleaned}'. Tenant ID must be 1-64 characters, "
            "start with an alphanumeric character, and contain only letters, numbers, "
            "underscores, or hyphens."
        )

    return cleaned


def get_tenants_base_dir(custom_base: Optional[Path | str] = None) -> Path:
    """Return the resolved base directory for tenants."""
    if custom_base:
        return Path(custom_base).resolve()
    return DEFAULT_TENANTS_DIR.resolve()


def resolve_tenant_dir(
    tenant_id: str, tenants_base_dir: Optional[Path | str] = None
) -> Path:
    """Resolve and validate the tenant directory path."""
    clean_id = validate_tenant_id(tenant_id)
    base_dir = get_tenants_base_dir(tenants_base_dir)

    tenant_dir = (base_dir / clean_id).resolve()

    # Verify no traversal escaped the base directory
    try:
        tenant_dir.relative_to(base_dir)
    except ValueError:
        raise ValueError(
            f"Tenant path traversal detected: tenant '{clean_id}' escapes base directory."
        )

    return tenant_dir


def resolve_tenant_config_path(
    tenant_id: str, tenants_base_dir: Optional[Path | str] = None
) -> Path:
    """Resolve the path to a tenant's config.toml file."""
    tenant_dir = resolve_tenant_dir(tenant_id, tenants_base_dir)
    if not tenant_dir.exists():
        raise FileNotFoundError(f"Tenant directory not found: {tenant_dir}")
    if not tenant_dir.is_dir():
        raise ValueError(f"Tenant path is not a directory: {tenant_dir}")

    config_file = tenant_dir / "config.toml"
    if not config_file.exists():
        raise FileNotFoundError(f"Tenant configuration not found: {config_file}")
    if not config_file.is_file():
        raise ValueError(f"Tenant configuration is not a file: {config_file}")

    # Validate that symlinks do not escape tenant directory
    resolved_config = config_file.resolve()
    try:
        resolved_config.relative_to(tenant_dir)
    except ValueError:
        raise ValueError(
            f"Tenant symlink escape detected: {config_file} resolves outside {tenant_dir}."
        )

    return resolved_config


def validate_tenant_path_isolation(
    resolved_target: Path,
    tenant_dir: Path,
    tenants_base_dir: Optional[Path | str] = None,
) -> None:
    """Ensure that a resolved path cannot access another tenant's private directory.

    If the resolved target resides under the tenants base directory, it MUST
    reside strictly under this tenant's directory.
    """
    base_dir = get_tenants_base_dir(tenants_base_dir)
    canonical_target = resolved_target.resolve()
    canonical_base = base_dir.resolve()
    canonical_tenant = tenant_dir.resolve()

    # Check if target is inside the tenants root directory
    try:
        canonical_target.relative_to(canonical_base)
        is_under_tenants_base = True
    except ValueError:
        is_under_tenants_base = False

    if is_under_tenants_base:
        # It is inside tenants base directory. It must be inside this tenant's directory.
        try:
            canonical_target.relative_to(canonical_tenant)
        except ValueError:
            raise ValueError(
                f"Cross-tenant access violation: path '{resolved_target}' resolves "
                f"into another tenant's directory ({canonical_target})."
            )


def list_tenants(tenants_base_dir: Optional[Path | str] = None) -> List[str]:
    """List valid configured tenant IDs in the tenants directory."""
    base_dir = get_tenants_base_dir(tenants_base_dir)
    if not base_dir.exists() or not base_dir.is_dir():
        return []

    tenants = []
    for item in sorted(base_dir.iterdir()):
        if item.is_dir():
            try:
                tid = validate_tenant_id(item.name)
                if (item / "config.toml").is_file():
                    tenants.append(tid)
            except ValueError:
                continue
    return tenants
