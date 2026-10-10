"""Tests for multi-tenant directory convention, path isolation, and config loading."""

from pathlib import Path
from types import SimpleNamespace
import pytest

from rss_morning import cli
from rss_morning.config import parse_app_config
from rss_morning.tenant import (
    list_tenants,
    resolve_tenant_config_path,
    resolve_tenant_dir,
    validate_tenant_id,
    validate_tenant_path_isolation,
)


def test_validate_tenant_id_valid():
    for valid_id in ["security", "global-markets", "tenant_1", "corp-sec-2026", "A1"]:
        assert validate_tenant_id(valid_id) == valid_id


def test_validate_tenant_id_invalid():
    invalid_cases = [
        "",
        "   ",
        "tenant/subtenant",
        "tenant\\subtenant",
        "../escape",
        "tenant..",
        ".hidden",
        "tenant.txt",
        "tenant with space",
        "@tenant",
        "tenant!",
        123,
        None,
    ]
    for case in invalid_cases:
        with pytest.raises(ValueError):
            validate_tenant_id(case)  # type: ignore


def test_resolve_tenant_dir(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenants_dir.mkdir()
    tenant_dir = tenants_dir / "sec-team"
    tenant_dir.mkdir()

    resolved = resolve_tenant_dir("sec-team", tenants_base_dir=tenants_dir)
    assert resolved == tenant_dir.resolve()


def test_resolve_tenant_config_path_success(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenant_dir = tenants_dir / "sec-team"
    tenant_dir.mkdir(parents=True)
    config_file = tenant_dir / "config.toml"
    config_file.touch()

    resolved = resolve_tenant_config_path("sec-team", tenants_base_dir=tenants_dir)
    assert resolved == config_file.resolve()


def test_resolve_tenant_config_missing_dir(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenants_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="Tenant directory not found"):
        resolve_tenant_config_path("missing-tenant", tenants_base_dir=tenants_dir)


def test_resolve_tenant_config_missing_file(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenant_dir = tenants_dir / "empty-tenant"
    tenant_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="Tenant configuration not found"):
        resolve_tenant_config_path("empty-tenant", tenants_base_dir=tenants_dir)


def test_resolve_tenant_config_symlink_escape(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenant_dir = tenants_dir / "evil-tenant"
    tenant_dir.mkdir(parents=True)

    outside_file = tmp_path / "outside_config.toml"
    outside_file.touch()

    symlink_config = tenant_dir / "config.toml"
    symlink_config.symlink_to(outside_file)

    with pytest.raises(ValueError, match="Tenant symlink escape detected"):
        resolve_tenant_config_path("evil-tenant", tenants_base_dir=tenants_dir)


def test_tenant_path_isolation_allows_internal_and_shared(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenant_a = tenants_dir / "tenant-a"
    tenant_a.mkdir(parents=True)
    internal_file = tenant_a / "prompt.md"
    internal_file.touch()

    shared_root_file = tmp_path / "shared_feeds.xml"
    shared_root_file.touch()

    # Accessing files within own tenant directory is allowed
    validate_tenant_path_isolation(
        internal_file, tenant_a, tenants_base_dir=tenants_dir
    )

    # Accessing shared files outside the tenants base directory is allowed
    validate_tenant_path_isolation(
        shared_root_file, tenant_a, tenants_base_dir=tenants_dir
    )


def test_tenant_path_isolation_rejects_cross_tenant_access(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenant_a = tenants_dir / "tenant-a"
    tenant_b = tenants_dir / "tenant-b"
    tenant_a.mkdir(parents=True)
    tenant_b.mkdir(parents=True)

    tenant_b_secret = tenant_b / "prompt.md"
    tenant_b_secret.touch()

    # Tenant A attempting to access Tenant B's files must fail
    with pytest.raises(ValueError, match="Cross-tenant access violation"):
        validate_tenant_path_isolation(
            tenant_b_secret, tenant_a, tenants_base_dir=tenants_dir
        )


def test_tenant_path_isolation_rejects_symlink_cross_tenant(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenant_a = tenants_dir / "tenant-a"
    tenant_b = tenants_dir / "tenant-b"
    tenant_a.mkdir(parents=True)
    tenant_b.mkdir(parents=True)

    tenant_b_secret = tenant_b / "holdings.toml"
    tenant_b_secret.touch()

    symlink_in_a = tenant_a / "evil_link.toml"
    symlink_in_a.symlink_to(tenant_b_secret)

    with pytest.raises(ValueError, match="Cross-tenant access violation"):
        validate_tenant_path_isolation(
            symlink_in_a, tenant_a, tenants_base_dir=tenants_dir
        )


def test_list_tenants(tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenants_dir.mkdir()

    for t_name in ["tenant-1", "tenant-2"]:
        t_dir = tenants_dir / t_name
        t_dir.mkdir()
        (t_dir / "config.toml").touch()

    # Directory without config.toml
    (tenants_dir / "empty-tenant").mkdir()

    found = list_tenants(tenants_base_dir=tenants_dir)
    assert found == ["tenant-1", "tenant-2"]


def test_parse_app_config_unknown_setting_raises(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        """
        feeds = "feeds.xml"
        bogus_setting = "invalid"
        """,
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError, match="Unknown configuration setting 'bogus_setting'"
    ):
        parse_app_config(str(config))


def test_parse_app_config_extractor_validation(tmp_path):
    feeds = tmp_path / "feeds.xml"
    feeds.touch()

    # Valid extractor
    valid_cfg = tmp_path / "valid.toml"
    valid_cfg.write_text(
        """
        feeds = "feeds.xml"
        extractor = "trafilatura"
        """,
        encoding="utf-8",
    )
    parsed = parse_app_config(str(valid_cfg))
    assert parsed.extractor == "trafilatura"

    # Unsupported extractor
    invalid_cfg = tmp_path / "invalid.toml"
    invalid_cfg.write_text(
        """
        feeds = "feeds.xml"
        extractor = "newspaper3k"
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unsupported extractor 'newspaper3k'"):
        parse_app_config(str(invalid_cfg))


def test_parse_app_config_cross_tenant_reference_rejected(tmp_path, monkeypatch):
    tenants_dir = tmp_path / "tenants"
    monkeypatch.setattr("rss_morning.config.get_tenants_base_dir", lambda: tenants_dir)

    tenant_a = tenants_dir / "tenant-a"
    tenant_b = tenants_dir / "tenant-b"
    tenant_a.mkdir(parents=True)
    tenant_b.mkdir(parents=True)

    (tenant_a / "feeds.xml").touch()
    (tenant_b / "secret_prompt.md").touch()

    # Tenant A tries relative path traversal to load Tenant B's prompt
    cfg_a = tenant_a / "config.toml"
    cfg_a.write_text(
        """
        feeds = "feeds.xml"
        prompt_file = "../tenant-b/secret_prompt.md"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Cross-tenant access violation"):
        parse_app_config(str(cfg_a))


def test_sanitized_example_tenants_parse_cleanly():
    sec_cfg_path = Path("configs/tenants/example-security/config.toml")
    markets_cfg_path = Path("configs/tenants/example-markets/config.toml")

    assert sec_cfg_path.is_file(), "example-security config must exist"
    assert markets_cfg_path.is_file(), "example-markets config must exist"

    sec_config = parse_app_config(str(sec_cfg_path))
    assert sec_config.tenant_id == "example-security"
    assert sec_config.profile == "security"
    assert sec_config.email.to_addr == "security-team@example.com"
    assert "mobile_security" in sec_config.areas

    markets_config = parse_app_config(str(markets_cfg_path))
    assert markets_config.tenant_id == "example-markets"
    assert markets_config.profile == "markets"
    assert markets_config.email.to_addr == "markets-briefing@example.com"
    assert "macro_fx" in markets_config.areas
    assert "equities_etfs" in markets_config.areas


def test_cli_tenant_invocation(monkeypatch, tmp_path):
    tenants_dir = tmp_path / "tenants"
    tenant_dir = tenants_dir / "my-tenant"
    tenant_dir.mkdir(parents=True)
    cfg_file = tenant_dir / "config.toml"
    (tenant_dir / "feeds.xml").touch()
    cfg_file.write_text('feeds = "feeds.xml"\nprofile = "security"\n', encoding="utf-8")

    monkeypatch.setattr(
        "rss_morning.cli.resolve_tenant_config_path", lambda tid: cfg_file
    )
    monkeypatch.setattr(
        "rss_morning.cli.configure_logging", lambda lvl, log_file=None: None
    )

    captured = {}

    def fake_execute(run_cfg):
        captured["run_cfg"] = run_cfg
        return SimpleNamespace(output_text="{}", email_payload=None, is_summary=False)

    monkeypatch.setattr("rss_morning.cli.execute", fake_execute)

    exit_code = cli.main(["--tenant", "my-tenant"])
    assert exit_code == 0
    assert captured["run_cfg"].tenant_id == "my-tenant"


def test_cli_mutually_exclusive_config_and_tenant():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--config", "configs/config.toml", "--tenant", "sample"])
