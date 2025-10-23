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
