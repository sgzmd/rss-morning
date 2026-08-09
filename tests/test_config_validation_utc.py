import os
import time
from datetime import datetime, timezone

import pytest

from rss_morning import cli, feeds
from rss_morning.config import parse_app_config


def _config(tmp_path, fragment: str):
    (tmp_path / "feeds.xml").write_text("<opml><body /></opml>", encoding="utf-8")
    path = tmp_path / "config.xml"
    path.write_text(
        f"<config><feeds>feeds.xml</feeds>{fragment}</config>", encoding="utf-8"
    )
    return path


@pytest.mark.parametrize(
    "fragment,path,value",
    [
        ("<limit>0</limit>", "limit", "0"),
        ("<max-age-hours>-1</max-age-hours>", "max-age-hours", "-1"),
        ("<max-article-length>0</max-article-length>", "max-article-length", "0"),
        ("<concurrency>0</concurrency>", "concurrency", "0"),
        ("<extractor>unknown</extractor>", "extractor", "unknown"),
        (
            "<embeddings><provider>other</provider></embeddings>",
            "embeddings/provider",
            "other",
        ),
        ("<embeddings><model> </model></embeddings>", "embeddings/model", "empty"),
        ("<llm><provider>other</provider></llm>", "llm/provider", "other"),
        ("<llm><model> </model></llm>", "llm/model", "empty"),
        (
            "<pre-filter><cluster-threshold>1.1</cluster-threshold></pre-filter>",
            "pre-filter/cluster-threshold",
            "1.1",
        ),
        (
            "<pre-filter><candidate-multiplier>0</candidate-multiplier></pre-filter>",
            "pre-filter/candidate-multiplier",
            "0",
        ),
        (
            "<pre-filter><max-cluster-size>0</max-cluster-size></pre-filter>",
            "pre-filter/max-cluster-size",
            "0",
        ),
        (
            "<http><connect-timeout>0</connect-timeout></http>",
            "http/connect-timeout",
            "0",
        ),
        ("<http><read-timeout>-1</read-timeout></http>", "http/read-timeout", "-1"),
        ("<http><max-feed-bytes>0</max-feed-bytes></http>", "http/max-feed-bytes", "0"),
        (
            "<http><max-article-bytes>-1</max-article-bytes></http>",
            "http/max-article-bytes",
            "-1",
        ),
        ("<http><retries>-1</retries></http>", "http/retries", "-1"),
        (
            "<http><backoff-seconds>-1</backoff-seconds></http>",
            "http/backoff-seconds",
            "-1",
        ),
        (
            "<http><per-host-concurrency>0</per-host-concurrency></http>",
            "http/per-host-concurrency",
            "0",
        ),
        (
            "<llm><max-input-price-per-million>0</max-input-price-per-million></llm>",
            "llm/max-input-price-per-million",
            "0",
        ),
        (
            "<llm><max-output-price-per-million>nan</max-output-price-per-million></llm>",
            "llm/max-output-price-per-million",
            "nan",
        ),
        (
            "<llm><max-batch-articles>0</max-batch-articles></llm>",
            "llm/max-batch-articles",
            "0",
        ),
        (
            "<llm><max-input-tokens>0</max-input-tokens></llm>",
            "llm/max-input-tokens",
            "0",
        ),
        (
            "<llm><request-timeout-seconds>0</request-timeout-seconds></llm>",
            "llm/request-timeout-seconds",
            "0",
        ),
        ("<llm><max-attempts>0</max-attempts></llm>", "llm/max-attempts", "0"),
        (
            "<llm><max-split-depth>-1</max-split-depth></llm>",
            "llm/max-split-depth",
            "-1",
        ),
    ],
)
def test_invalid_config_names_exact_path_and_safe_value(
    tmp_path, fragment, path, value
):
    with pytest.raises(ValueError) as caught:
        parse_app_config(str(_config(tmp_path, fragment)))
    message = str(caught.value)
    assert path in message
    assert value in message


@pytest.mark.parametrize(
    "fragment,path",
    [
        ("<summary>tru</summary>", "summary"),
        ("<pre-filter><enabled>yes</enabled></pre-filter>", "pre-filter/enabled"),
        ("<database><enabled>1</enabled></database>", "database/enabled"),
        (
            "<llm><require-structured-output>TRUE-ish</require-structured-output></llm>",
            "llm/require-structured-output",
        ),
        ("<llm><cache-enabled>nope</cache-enabled></llm>", "llm/cache-enabled"),
    ],
)
def test_malformed_booleans_are_rejected(tmp_path, fragment, path):
    with pytest.raises(ValueError, match=path):
        parse_app_config(str(_config(tmp_path, fragment)))


@pytest.mark.parametrize(
    "fragment,path",
    [
        ("<summary>true</summary>", "prompt"),
        ("<email><to>reader@example.com</to></email>", "email/from"),
    ],
)
def test_cross_field_validation_happens_during_config_parse(
    tmp_path, monkeypatch, fragment, path
):
    monkeypatch.delenv("RESEND_FROM_EMAIL", raising=False)
    with pytest.raises(ValueError, match=path):
        parse_app_config(str(_config(tmp_path, fragment)))


def test_email_sender_environment_fallback_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_FROM_EMAIL", "sender@example.com")
    config = parse_app_config(
        str(_config(tmp_path, "<email><to>reader@example.com</to></email>"))
    )
    assert config.email.to_addr == "reader@example.com"


def test_malformed_number_text_and_empty_prompt_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="limit"):
        parse_app_config(str(_config(tmp_path, "<limit>many</limit>")))
    with pytest.raises(ValueError, match="max-age-hours"):
        parse_app_config(
            str(_config(tmp_path, "<max-age-hours>recent</max-age-hours>"))
        )
    (tmp_path / "prompt.txt").write_text("   ", encoding="utf-8")
    with pytest.raises(ValueError, match="prompt"):
        parse_app_config(str(_config(tmp_path, '<prompt file="prompt.txt" />')))


def test_known_catalog_price_over_cap_warns_without_failing(tmp_path, caplog):
    config = parse_app_config(
        str(
            _config(
                tmp_path,
                """<llm><model>google/gemini-2.5-flash</model>
                <fallback-models />
                <max-input-price-per-million>0.20</max-input-price-per-million>
                <max-output-price-per-million>0.75</max-output-price-per-million>
                </llm>""",
            )
        )
    )
    assert config.llm.model == "google/gemini-2.5-flash"
    assert "llm/model" in caplog.text
    assert "price cap" in caplog.text


def test_invalid_config_exits_before_external_boundaries(tmp_path, monkeypatch):
    config = _config(tmp_path, "<limit>0</limit>")
    calls = []
    monkeypatch.setattr(cli, "parse_env_config", lambda *_: calls.append("env"))
    monkeypatch.setattr(cli, "configure_logging", lambda *_: calls.append("logging"))
    monkeypatch.setattr(cli, "execute", lambda *_: calls.append("execute"))

    assert cli.main(["--config", str(config)]) == 1
    assert calls == []


def test_missing_sender_in_env_is_rejected_before_runtime(tmp_path, monkeypatch):
    config = _config(
        tmp_path,
        "<env>env.xml</env><email><to>reader@example.com</to></email>",
    )
    (tmp_path / "env.xml").write_text("<environment />", encoding="utf-8")
    calls = []
    monkeypatch.delenv("RESEND_FROM_EMAIL", raising=False)
    monkeypatch.setattr(cli, "parse_env_config", lambda *_: calls.append("env") or {})
    monkeypatch.setattr(cli, "configure_logging", lambda *_: calls.append("logging"))
    monkeypatch.setattr(cli, "execute", lambda *_: calls.append("execute"))

    assert cli.main(["--config", str(config)]) == 1
    assert calls == ["env"]


def test_feed_timestamp_is_independent_of_process_timezone(monkeypatch):
    stamp = time.struct_time((2025, 7, 1, 12, 34, 56, 1, 182, 0))
    original = os.environ.get("TZ")
    values = []
    try:
        for zone in ("UTC", "Europe/London", "America/New_York"):
            monkeypatch.setenv("TZ", zone)
            time.tzset()
            values.append(feeds.to_datetime(stamp))
    finally:
        if original is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original)
        time.tzset()

    assert values == [datetime(2025, 7, 1, 12, 34, 56, tzinfo=timezone.utc)] * 3


def test_missing_feed_date_is_minimum_utc_and_sorts_last():
    missing = feeds.to_datetime(None)
    dated = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert missing == datetime.min.replace(tzinfo=timezone.utc)
    assert sorted([missing, dated], reverse=True) == [dated, missing]
