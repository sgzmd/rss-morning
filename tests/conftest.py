import contextlib
import pathlib
import shutil
import sys
import types

import pytest


def _ensure_module(name: str, module):
    sys.modules.setdefault(name, module)


# Provide lightweight stubs for optional third-party dependencies to keep tests hermetic.
fake_requests = types.ModuleType("requests")
fake_requests.get = lambda *args, **kwargs: (_ for _ in ()).throw(
    RuntimeError("requests stub")
)


class FakeDocument:
    def __init__(self, text):
        self.text = text

    def summary(self, html_partial=True):
        raise RuntimeError("readability stub")


fake_readability = types.ModuleType("readability")
fake_readability.Document = FakeDocument

fake_html = types.ModuleType("html")
fake_html.fromstring = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    RuntimeError("lxml stub")
)

fake_lxml = types.ModuleType("lxml")
fake_lxml.html = fake_html

fake_feedparser = types.ModuleType("feedparser")
fake_feedparser.parse = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    RuntimeError("feedparser stub")
)

_ensure_module("requests", fake_requests)
_ensure_module("readability", fake_readability)
_ensure_module("lxml", fake_lxml)
_ensure_module("lxml.html", fake_html)
_ensure_module("feedparser", fake_feedparser)


@pytest.fixture(scope="session", autouse=True)
def _ensure_prompt_file():
    """Ensure prompt.md and feeds.xml exist for tests by copying from templates."""
    project_root = pathlib.Path.cwd()

    prompt_example = project_root / "prompt-example.md"
    prompt_target = project_root / "prompt.md"
    feeds_example = project_root / "feeds.example.xml"
    feeds_target = project_root / "feeds.xml"

    created_prompt = False
    created_feeds = False

    if prompt_example.exists() and not prompt_target.exists():
        shutil.copy(prompt_example, prompt_target)
        created_prompt = True

    if feeds_example.exists() and not feeds_target.exists():
        shutil.copy(feeds_example, feeds_target)
        created_feeds = True

    try:
        yield
    finally:
        if created_prompt:
            with contextlib.suppress(OSError):
                prompt_target.unlink()
        if created_feeds:
            with contextlib.suppress(OSError):
                feeds_target.unlink()
