from rss_morning import prefilter_cli


def test_prefilter_cli_main_invokes_export(monkeypatch, tmp_path):
    called = {}

    def fake_export(output_path, *, config, client=None):
        called["output_path"] = output_path
        called["config"] = config
        return tmp_path / "written.json"

    monkeypatch.setattr(prefilter_cli, "configure_logging", lambda: None)
    monkeypatch.setattr(prefilter_cli, "export_security_query_embeddings", fake_export)
    monkeypatch.setattr(
        prefilter_cli.EmbeddingArticleFilter,
        "QUERIES",
        ("Q1", "Q2"),
        raising=False,
    )

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
