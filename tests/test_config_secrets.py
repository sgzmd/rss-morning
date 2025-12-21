import os
import pytest
from unittest.mock import patch, MagicMock
from rss_morning.config import load_secrets


@pytest.fixture
def clean_env():
    """Ensure relevant environment variables are unset before/after tests."""
    keys = ["OPENAI_API_KEY", "GOOGLE_API_KEY", "RESEND_API_KEY", "RESEND_FROM_EMAIL"]
    original = {k: os.environ.get(k) for k in keys}
    for k in keys:
        if k in os.environ:
            del os.environ[k]
    yield
    for k, v in original.items():
        if v is not None:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]


def populate_all_secrets(defaults=None):
    if defaults is None:
        defaults = {}
    base = {
        "OPENAI_API_KEY": "dummy-openai",
        "GOOGLE_API_KEY": "dummy-google",
        "RESEND_API_KEY": "dummy-resend",
        "RESEND_FROM_EMAIL": "dummy-email",
    }
    base.update(defaults)
    for k, v in base.items():
        os.environ[k] = v


def test_load_secrets_from_env(clean_env):
    populate_all_secrets({"OPENAI_API_KEY": "env-openai"})
    secrets = load_secrets(env_file=None)
    assert secrets.openai_api_key == "env-openai"
    assert secrets.google_api_key == "dummy-google"


def test_load_secrets_from_xml(clean_env, tmp_path):
    # We populate missing ones via env for validity
    os.environ["OPENAI_API_KEY"] = "dummy-openai"
    os.environ["RESEND_API_KEY"] = "dummy-resend"
    os.environ["RESEND_FROM_EMAIL"] = "dummy-email"

    env_xml = tmp_path / "env.xml"
    env_xml.write_text("""
    <environment>
        <variable name="GOOGLE_API_KEY">xml-google</variable>
    </environment>
    """)
    secrets = load_secrets(env_file=str(env_xml))
    assert secrets.google_api_key == "xml-google"
    assert secrets.openai_api_key == "dummy-openai"


def test_strict_conflict_env_and_xml(clean_env, tmp_path):
    populate_all_secrets({"OPENAI_API_KEY": "env-openai"})
    env_xml = tmp_path / "env.xml"
    env_xml.write_text("""
    <environment>
        <variable name="OPENAI_API_KEY">xml-openai</variable>
    </environment>
    """)

    with pytest.raises(ValueError, match="Secret conflict for 'openai_api_key'"):
        load_secrets(env_file=str(env_xml))


@patch("rss_morning.config.boto3")
def test_load_secrets_ssm(mock_boto3, clean_env):
    mock_ssm = MagicMock()
    mock_boto3.client.return_value = mock_ssm

    # Mock return values for parameters
    def get_parameter(Name, WithDecryption):
        if Name == "/rss-morning/OPENAI_API_KEY":
            return {"Parameter": {"Value": "ssm-openai"}}
        if Name == "/rss-morning/GOOGLE_API_KEY":
            return {"Parameter": {"Value": "ssm-google"}}
        if Name == "/rss-morning/RESEND_API_KEY":
            return {"Parameter": {"Value": "ssm-resend"}}
        if Name == "/rss-morning/RESEND_FROM_EMAIL":
            return {"Parameter": {"Value": "ssm-email"}}
        raise Exception("Not found")

    mock_ssm.get_parameter.side_effect = get_parameter

    secrets = load_secrets(env_file=None, use_ssm=True, ssm_prefix="/rss-morning")
    assert secrets.openai_api_key == "ssm-openai"
    assert secrets.google_api_key == "ssm-google"


@patch("rss_morning.config.boto3")
def test_strict_conflict_ssm_and_env(mock_boto3, clean_env):
    # Ensure all secrets are present to pass mismatch check, but invoke conflict
    populate_all_secrets({"RESEND_API_KEY": "env-resend"})

    mock_ssm = MagicMock()
    mock_boto3.client.return_value = mock_ssm

    # Helper to return SSM value only for RESEND_API_KEY
    def get_parameter(Name, WithDecryption):
        if Name == "/rss-morning/RESEND_API_KEY":
            return {"Parameter": {"Value": "ssm-resend"}}
        raise Exception("Not found")

    mock_ssm.get_parameter.side_effect = get_parameter

    # Map RESEND_API_KEY to checking conflict
    with pytest.raises(ValueError, match="Secret conflict for 'resend_api_key'"):
        # We need to simulate the implementation of load_secrets which calls get_parameter
        # load_secrets iterates all keys, so it will ask for RESEND_API_KEY
        load_secrets(env_file=None, use_ssm=True)


def test_load_mixed_sources_no_conflict(clean_env, tmp_path):
    # OPENAI from Env
    os.environ["OPENAI_API_KEY"] = "env-openai"
    os.environ["RESEND_API_KEY"] = "dummy-resend"
    os.environ["RESEND_FROM_EMAIL"] = "dummy-email"

    # GOOGLE from XML
    env_xml = tmp_path / "env.xml"
    env_xml.write_text("""
    <environment>
        <variable name="GOOGLE_API_KEY">xml-google</variable>
    </environment>
    """)

    secrets = load_secrets(env_file=str(env_xml))
    assert secrets.openai_api_key == "env-openai"
    assert secrets.google_api_key == "xml-google"


def test_missing_secrets_fails(clean_env):
    # Only provide one
    os.environ["OPENAI_API_KEY"] = "env-openai"

    with pytest.raises(ValueError, match="Missing required secrets"):
        load_secrets(env_file=None)
