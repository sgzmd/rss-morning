import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rss_morning import summaries
from rss_morning.config import parse_app_config


def _article(number: int, *, text: str = "body") -> dict:
    return {
        "url": f"https://fixture.invalid/{number}",
        "category": f"Category {number}",
        "title": f"Title {number}",
        "summary": f"Feed summary {number}",
        "text": text,
    }


def _payload(articles: list[dict], *, model_category: str = "untrusted") -> dict:
    return {
        "exec-summary": ["<b>Executive</b>"],
        "summaries": [
            {
                "url": article["url"],
                "category": model_category,
                "summary": {
                    "title": f"<b>{article['title']}</b>",
                    "rank-reasoning": "<i>Relevant</i>",
                    "what": "<p>What</p>",
                    "so-what": "<p>Why</p>",
                    "now-what": "<p>Act</p>",
                },
            }
            for article in reversed(articles)
        ],
    }


def _write_config(tmp_path: Path, llm_xml: str = ""):
    (tmp_path / "feeds.xml").write_text("<opml><body /></opml>", encoding="utf-8")
    config = tmp_path / "config.xml"
    config.write_text(
        f"<config><feeds>feeds.xml</feeds>{llm_xml}</config>", encoding="utf-8"
    )
    return parse_app_config(str(config))


def test_llm_defaults_and_complete_openrouter_configuration(tmp_path):
    default = _write_config(tmp_path)
    assert default.llm == summaries.SummarySettings()

    configured = _write_config(
        tmp_path,
        """<llm>
        <provider>openrouter</provider><model>vendor/primary</model>
        <fallback-models><model>vendor/first</model><model>vendor/second</model></fallback-models>
        <routing>latency</routing>
        <max-input-price-per-million>0.12</max-input-price-per-million>
        <max-output-price-per-million>0.64</max-output-price-per-million>
        <require-structured-output>true</require-structured-output>
        <max-batch-articles>7</max-batch-articles><max-input-tokens>900</max-input-tokens>
        <request-timeout-seconds>12</request-timeout-seconds><max-attempts>2</max-attempts>
        <max-split-depth>3</max-split-depth><cache-enabled>false</cache-enabled>
        </llm>""",
    )
    assert configured.llm == summaries.SummarySettings(
        model="vendor/primary",
        fallback_models=("vendor/first", "vendor/second"),
        routing="latency",
        max_input_price_per_million=0.12,
        max_output_price_per_million=0.64,
        max_batch_articles=7,
        max_input_tokens=900,
        request_timeout_seconds=12,
        max_attempts=2,
        max_split_depth=3,
        cache_enabled=False,
    )


@pytest.mark.parametrize(
    "llm_xml,match",
    [
        ("<llm><model>not-a-slug</model></llm>", "OpenRouter model"),
        (
            "<llm><model>v/a</model><fallback-models><model>v/a</model></fallback-models></llm>",
            "duplicate",
        ),
        (
            "<llm><fallback-models><model>a/1</model><model>a/2</model><model>a/3</model><model>a/4</model></fallback-models></llm>",
            "zero to three",
        ),
        ("<llm><routing>random</routing></llm>", "routing"),
    ],
)
def test_openrouter_config_validation(tmp_path, llm_xml, match):
    with pytest.raises(ValueError, match=match):
        _write_config(tmp_path, llm_xml)


def test_direct_gemini_configuration_does_not_require_openrouter_slug(tmp_path):
    config = _write_config(
        tmp_path,
        "<llm><provider>gemini</provider><model>gemini-flash-latest</model></llm>",
    )
    assert config.llm.provider == "gemini"
    assert config.llm.model == "gemini-flash-latest"
    assert config.llm.fallback_models == ()


def test_catalog_fixture_proves_default_caps_and_is_not_runtime_dependency(monkeypatch):
    fixture = json.loads(
        Path("tests/fixtures/openrouter_models_2026-08-09.json").read_text()
    )
    settings = summaries.SummarySettings()
    admitted = summaries.models_within_caps(fixture, settings)
    assert admitted == [settings.model, *settings.fallback_models]
    assert "google/gemini-2.5-flash" not in admitted
    monkeypatch.setattr(
        Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError())
    )
    assert summaries.cache_identity(settings, "prompt", [_article(1)])


def test_batch_planner_is_deterministic_bounded_and_counts_prompt():
    articles = [_article(i, text="x" * 90) for i in range(5)]
    first = summaries.plan_batches(
        articles, "p" * 80, max_articles=3, max_input_tokens=130
    )
    second = summaries.plan_batches(
        articles, "p" * 80, max_articles=3, max_input_tokens=130
    )
    assert first == second
    assert [len(batch) for batch in first] == [2, 2, 1]
    assert all(
        summaries.estimate_input_tokens("p" * 80, batch) <= 130 for batch in first
    )

    oversized = summaries.plan_batches(
        [_article(9, text="x" * 10_000)],
        "prompt",
        max_articles=2,
        max_input_tokens=100,
    )
    assert len(oversized) == 1 and len(oversized[0]) == 1
    assert summaries.estimate_input_tokens("prompt", oversized[0]) <= 100


def test_openrouter_request_has_ordered_fallbacks_caps_timeout_and_metrics(monkeypatch):
    article = _article(1)
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                model="z-ai/glm-4.7-flash",
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(_payload([article])))
                    )
                ],
            )

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(summaries, "OpenAI", Client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_API_KEY", "must-not-be-used")
    metrics = []
    settings = summaries.SummarySettings(request_timeout_seconds=17)
    _, result = summaries.generate_summary(
        [article],
        "prompt",
        return_dict=True,
        settings=settings,
        metrics_sink=metrics.append,
    )
    assert result["summaries"][0]["category"] == article["category"]
    assert captured["timeout"] == 17
    assert captured["extra_body"] == {
        "models": list(settings.fallback_models),
        "provider": {
            "require_parameters": True,
            "sort": "price",
            "max_price": {"prompt": 0.20, "completion": 0.75},
        },
    }
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert metrics == [
        {"model": "z-ai/glm-4.7-flash", "input_tokens": 100, "output_tokens": 20}
    ]
    assert "model" not in result and "usage" not in result


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionError(),
        summaries.ProviderError(429, retry_after=7),
        summaries.ProviderError(503),
    ],
)
def test_transient_failures_retry_with_injected_sleeper(monkeypatch, error):
    article = _article(1)
    calls = 0
    sleeps = []

    def request(_batch, _input):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return summaries.ProviderResponse(
            json.dumps(_payload([article])), "vendor/model", 1, 1
        )

    settings = summaries.SummarySettings(
        model="vendor/model", fallback_models=(), max_attempts=2, cache_enabled=False
    )
    result = summaries.generate_summary(
        [article],
        "prompt",
        settings=settings,
        provider_request=request,
        sleeper=sleeps.append,
    )
    assert len(json.loads(result)["summaries"]) == 1
    assert calls == 2
    assert sleeps == [7 if getattr(error, "retry_after", None) else 1.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_provider_errors_are_not_retried(status):
    calls = 0

    def request(_batch, _input):
        nonlocal calls
        calls += 1
        raise summaries.ProviderError(status)

    settings = summaries.SummarySettings(
        model="v/m", fallback_models=(), max_attempts=3, cache_enabled=False
    )
    result = summaries.generate_summary(
        [_article(1)],
        "p",
        settings=settings,
        provider_request=request,
        sleeper=lambda _: None,
    )
    assert json.loads(result) == {"summaries": []}
    assert calls == 1


def test_retry_exhaustion_splits_and_attempt_bound_is_exact():
    calls = []

    def request(batch, _input):
        calls.append([item["url"] for item in batch])
        if len(batch) > 1:
            raise TimeoutError()
        return summaries.ProviderResponse(json.dumps(_payload(batch)), "v/m", 1, 1)

    settings = summaries.SummarySettings(
        model="v/m",
        fallback_models=(),
        max_batch_articles=4,
        max_attempts=2,
        max_split_depth=2,
        cache_enabled=False,
    )
    articles = [_article(i) for i in range(4)]
    result = json.loads(
        summaries.generate_summary(
            articles,
            "p",
            settings=settings,
            provider_request=request,
            sleeper=lambda _: None,
        )
    )
    assert [item["url"] for item in result["summaries"]] == [a["url"] for a in articles]
    assert len(calls) == 6
    assert summaries.maximum_provider_attempts(4, 2, 2) == 14


@pytest.mark.parametrize(
    "mutation", ["hallucinated", "duplicate", "missing-field", "missing-item"]
)
def test_malformed_identity_or_content_is_never_accepted_or_cached(mutation):
    articles = [_article(1), _article(2)]
    payload = _payload(articles)
    if mutation == "hallucinated":
        payload["summaries"][0]["url"] = "https://attacker.invalid/"
    elif mutation == "duplicate":
        payload["summaries"][1]["url"] = payload["summaries"][0]["url"]
    elif mutation == "missing-field":
        payload["summaries"][0]["summary"]["what"] = ""
    else:
        payload["summaries"].pop()
    writes = []
    settings = summaries.SummarySettings(
        model="v/m", fallback_models=(), max_attempts=1, max_split_depth=0
    )
    result = summaries.generate_summary(
        articles,
        "p",
        settings=settings,
        provider_request=lambda *_: summaries.ProviderResponse(
            json.dumps(payload), "v/m", 1, 1
        ),
        cache_get=lambda _key: None,
        cache_put=lambda *args: writes.append(args),
        sleeper=lambda _: None,
    )
    assert json.loads(result) == {"summaries": []}
    assert writes == []


def test_reconciliation_sanitizes_every_field_restores_order_and_category():
    articles = [_article(1), _article(2)]
    parsed = summaries.reconcile_response(_payload(articles), articles)
    assert [item["url"] for item in parsed["summaries"]] == [a["url"] for a in articles]
    assert [item["category"] for item in parsed["summaries"]] == [
        a["category"] for a in articles
    ]
    assert parsed["exec-summary"] == ["Executive"]
    assert parsed["summaries"][0]["summary"] == {
        "title": "Title 1",
        "rank-reasoning": "Relevant",
        "what": "What",
        "so-what": "Why",
        "now-what": "Act",
    }


def test_exact_cache_identity_hits_and_all_semantic_inputs_miss():
    article = _article(1)
    base = summaries.SummarySettings(model="v/m", fallback_models=("v/f",))
    identity = summaries.cache_identity(base, "prompt", [article])
    variants = [
        (
            summaries.SummarySettings(model="v/m2", fallback_models=("v/f",)),
            "prompt",
            [article],
        ),
        (
            summaries.SummarySettings(model="v/m", fallback_models=("v/f2",)),
            "prompt",
            [article],
        ),
        (
            summaries.SummarySettings(
                model="v/m", fallback_models=("v/f",), routing="latency"
            ),
            "prompt",
            [article],
        ),
        (
            summaries.SummarySettings(
                model="v/m", fallback_models=("v/f",), max_input_price_per_million=0.19
            ),
            "prompt",
            [article],
        ),
        (base, "changed", [article]),
        (base, "prompt", [{**article, "text": "changed"}]),
        (base, "prompt", [article, _article(2)]),
    ]
    assert all(summaries.cache_identity(*variant) != identity for variant in variants)

    cached = json.dumps(summaries.reconcile_response(_payload([article]), [article]))
    calls = []
    result = summaries.generate_summary(
        [article],
        "prompt",
        settings=base,
        cache_get=lambda key: cached if key == identity else None,
        cache_put=lambda *_: None,
        provider_request=lambda *_: calls.append(True),
    )
    assert json.loads(result)["summaries"][0]["url"] == article["url"]
    assert calls == []


def test_corrupt_cache_and_cache_failures_fail_open_while_dry_run_skips_cache():
    article = _article(1)
    provider_calls = []
    cache_calls = []

    def request(batch, _input):
        provider_calls.append(True)
        return summaries.ProviderResponse(json.dumps(_payload(batch)), "v/m", 1, 1)

    settings = summaries.SummarySettings(
        model="v/m", fallback_models=(), max_attempts=1
    )
    result = summaries.generate_summary(
        [article],
        "p",
        settings=settings,
        provider_request=request,
        cache_get=lambda _key: "not json",
        cache_put=lambda *_: (_ for _ in ()).throw(OSError()),
    )
    assert len(json.loads(result)["summaries"]) == 1 and provider_calls == [True]

    dry = summaries.generate_summary(
        [article],
        "p",
        dry_run=True,
        settings=settings,
        cache_get=lambda _: cache_calls.append("read"),
        cache_put=lambda *_: cache_calls.append("write"),
        provider_request=request,
    )
    assert json.loads(dry) == {"dry_run": True}
    assert cache_calls == [] and provider_calls == [True]


def test_quality_corpus_covers_required_synthetic_cases():
    fixture = json.loads(Path("tests/fixtures/summary_quality_corpus.json").read_text())
    articles = fixture["articles"]
    assert len(articles) >= 8
    assert len({article["category"] for article in articles}) > 1
    assert any("東京" in article["title"] for article in articles)
    assert any("<script>" in article.get("text", "") for article in articles)
    assert any("text_repeat" in article for article in articles)
    assert fixture["required_summary_fields"] == list(summaries.REQUIRED_SUMMARY_FIELDS)
