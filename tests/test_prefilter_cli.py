import runpy
import sys

import pytest

from rss_morning import prefilter_cli


def test_prefilter_cli_main_invokes_export(monkeypatch, tmp_path):
    called = {}

    def fake_export(output_path, *, config, client=None, queries=None):
        called["output_path"] = output_path
        called["config"] = config
        called["queries"] = tuple(queries)
        return tmp_path / "written.json"

    monkeypatch.setattr(prefilter_cli, "configure_logging", lambda: None)
    monkeypatch.setattr(prefilter_cli, "export_security_query_embeddings", fake_export)

    def fake_load_queries(path):
        called["queries_file"] = path
        return ("Q1", "Q2")

    monkeypatch.setattr(prefilter_cli, "load_queries", fake_load_queries)

    dest = tmp_path / "export.json"
    exit_code = prefilter_cli.main(
        [
            "--output",
            str(dest),
            "--model",
            "fake-model",
            "--batch-size",
            "4",
            "--threshold",
            "0.9",
        ]
    )

    assert exit_code == 0
    assert called["output_path"] == str(dest)
    assert called["config"].model == "fake-model"
    assert called["config"].batch_size == 4
    assert called["config"].threshold == 0.9
    assert called["queries"] == ("Q1", "Q2")
    assert called["queries_file"] is None


def test_configure_logging_and_failed_export(monkeypatch, tmp_path):
    configured = {}
    monkeypatch.setattr(
        prefilter_cli.logging,
        "basicConfig",
        lambda **kwargs: configured.update(kwargs),
    )
    prefilter_cli.configure_logging()
    assert configured["level"] == prefilter_cli.logging.INFO

    monkeypatch.setattr(prefilter_cli, "configure_logging", lambda: None)
    monkeypatch.setattr(prefilter_cli, "load_queries", lambda _path: {"A": ("q",)})

    def fail(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(prefilter_cli, "export_security_query_embeddings", fail)
    assert prefilter_cli.main(["--output", str(tmp_path / "out.json")]) == 1


def test_module_entrypoint_exits_with_main_result(monkeypatch, tmp_path):
    from rss_morning import prefilter

    monkeypatch.setattr(prefilter, "load_queries", lambda _path: {"A": ("q",)})
    monkeypatch.setattr(
        prefilter,
        "export_security_query_embeddings",
        lambda *_args, **_kwargs: tmp_path / "out.json",
    )
    monkeypatch.setattr(
        sys, "argv", ["prefilter_cli", "--output", str(tmp_path / "out.json")]
    )

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("rss_morning.prefilter_cli", run_name="__main__")

    assert exc.value.code == 0
