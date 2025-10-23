import sys
import types


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
