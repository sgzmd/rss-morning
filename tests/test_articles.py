import importlib
import sys
import types


def _install_article_dependencies(
    monkeypatch, response_text="<p>Article body</p>", text_content="Article body"
):
    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=20):
        return FakeResponse(response_text)

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get

    class FakeDocument:
        def __init__(self, text):
            self._text = text

        def summary(self, html_partial=True):
            return self._text

    fake_readability = types.ModuleType("readability")
    fake_readability.Document = FakeDocument

    fake_html_module = types.ModuleType("html")

    class FakeParsed:
        def text_content(self):
            return text_content

    def fake_fromstring(_html):
        return FakeParsed()

    fake_html_module.fromstring = fake_fromstring
    fake_lxml = types.ModuleType("lxml")
    fake_lxml.html = fake_html_module

    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "readability", fake_readability)
    monkeypatch.setitem(sys.modules, "lxml", fake_lxml)
    monkeypatch.setitem(sys.modules, "lxml.html", fake_html_module)

    sys.modules.pop("rss_morning.articles", None)
    return importlib.import_module("rss_morning.articles")


def test_fetch_article_text_returns_parsed_content(monkeypatch):
    articles_module = _install_article_dependencies(monkeypatch)

    text = articles_module.fetch_article_text("https://example.com/article")

    assert text == "Article body"


def test_fetch_article_text_handles_request_errors(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            raise Exception("boom")

    def fake_get(url, timeout=20):
        return FakeResponse()

    fake_requests = types.ModuleType("requests")
    fake_requests.get = fake_get
    fake_requests.RequestException = Exception

    class FakeDocument:
        def __init__(self, text):
            self.text = text

        def summary(self, html_partial=True):
            return "<p></p>"

    fake_readability = types.ModuleType("readability")
    fake_readability.Document = FakeDocument

    fake_html_module = types.ModuleType("html")
    fake_html_module.fromstring = lambda html: types.SimpleNamespace(
        text_content=lambda: ""
    )

    fake_lxml = types.ModuleType("lxml")
    fake_lxml.html = fake_html_module

    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "readability", fake_readability)
    monkeypatch.setitem(sys.modules, "lxml", fake_lxml)
    monkeypatch.setitem(sys.modules, "lxml.html", fake_html_module)
    sys.modules.pop("rss_morning.articles", None)
    articles_module = importlib.import_module("rss_morning.articles")

    assert articles_module.fetch_article_text("https://example.com") is None


def test_truncate_text(monkeypatch):
    articles_module = _install_article_dependencies(monkeypatch)
    text = "x" * 1050

    truncated = articles_module.truncate_text(text, limit=100)

    assert len(truncated) == 100
