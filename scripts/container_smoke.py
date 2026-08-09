"""Offline assertions executed inside the final production image."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import pkgutil
import socket
import tempfile

import rss_morning
from rss_morning import cli
from rss_morning.articles import prepare_tokenizer, truncate_text


def _deny_socket(*_args, **_kwargs):
    raise AssertionError("container smoke attempted network access")


def main() -> int:
    if os.geteuid() == 0:
        raise AssertionError("runtime user must not be root")
    for name in ("pytest", "mypy", "ruff", "pre_commit", "virtualenv"):
        if importlib.util.find_spec(name) is not None:
            raise AssertionError(f"development package present: {name}")
    for module in pkgutil.iter_modules(rss_morning.__path__, "rss_morning."):
        importlib.import_module(module.name)

    socket.socket = _deny_socket
    prepare_tokenizer()
    if not truncate_text("one two three four", limit=2):
        raise AssertionError("offline token truncation returned no content")
    for key in (
        "OPENROUTER_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "RESEND_API_KEY",
    ):
        os.environ.pop(key, None)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "feeds.xml").write_text("<opml><body /></opml>", encoding="utf-8")
        (root / "prompt.txt").write_text("Summarize synthetic input.", encoding="utf-8")
        (root / "articles.json").write_text(
            json.dumps(
                [
                    {
                        "url": "https://fixture.invalid/article",
                        "category": "Synthetic",
                        "title": "Offline smoke",
                        "summary": "No external content.",
                        "text": "Bounded synthetic body.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (root / "config.xml").write_text(
            """<config>
            <feeds>feeds.xml</feeds><summary>true</summary>
            <prompt file="prompt.txt" />
            <llm><provider>openrouter</provider><cache-enabled>false</cache-enabled></llm>
            </config>""",
            encoding="utf-8",
        )
        result = cli.main(
            [
                "--config",
                str(root / "config.xml"),
                "--load-articles",
                str(root / "articles.json"),
                "--llm-dry-run",
            ]
        )
        if result != 0:
            raise AssertionError(f"offline snapshot dry run failed with {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
