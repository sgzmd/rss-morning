import importlib
import sys
import types


def _install_article_dependencies(
    monkeypatch,
    *,
    article_text="Article body",
    top_image="https://example.com/image.jpg",
    download_error=None,
    parse_error=None,
    parse_error_factory=None,
):
    class FakeArticle:
        def __init__(self, url, config):
            self.url = url
            self.config = config
            self.text = ""
            self.top_image = ""

        def set_html(self, html):
            self.html = html

        def parse(self):
            if parse_error_factory:
                raise parse_error_factory(FakeArticleException)
            if parse_error:
                raise parse_error
            self.text = article_text
            self.top_image = top_image

    fake_newspaper = types.ModuleType("newspaper")
    fake_newspaper.Article = FakeArticle

    class FakeConfig:
        def __init__(self):
            self.fetch_images = False
            self.memoize_articles = True
            self.request_timeout = None

    fake_newspaper.Config = FakeConfig

    class FakeArticleException(Exception):
        pass

    fake_article_module = types.ModuleType("newspaper.article")
    fake_article_module.ArticleException = FakeArticleException

    monkeypatch.setitem(sys.modules, "newspaper", fake_newspaper)
    monkeypatch.setitem(sys.modules, "newspaper.article", fake_article_module)

    sys.modules.pop("rss_morning.articles", None)
    articles_module = importlib.import_module("rss_morning.articles")

    class FakeHttpClient:
        def get(self, url, **_kwargs):
            if download_error or "fail" in url:
                error = download_error or Exception("download failed")
                raise articles_module.DownloadError("download failed") from error
            return types.SimpleNamespace(
                body=f"<html>{url}</html>".encode(), final_url=url
            )

    articles_module._DEFAULT_HTTP_CLIENT = FakeHttpClient()
    return articles_module, FakeArticleException


def test_fetch_article_content_returns_text_and_image(monkeypatch):
    articles_module, _ = _install_article_dependencies(monkeypatch)

    content = articles_module.fetch_article_content("https://example.com/article")

    assert content.text == "Article body"
    assert content.image == "https://example.com/image.jpg"


def test_fetch_article_content_handles_download_errors(monkeypatch):
    (
        articles_module,
        article_exception,
    ) = _install_article_dependencies(
        monkeypatch, download_error=Exception("download boom")
    )

    content = articles_module.fetch_article_content("https://example.com")

    assert content.text is None
    assert content.image is None


def test_fetch_article_content_handles_library_exceptions(monkeypatch):
    articles_module, _ = _install_article_dependencies(
        monkeypatch,
        parse_error_factory=lambda exc_cls: exc_cls("parse boom"),
    )

    content = articles_module.fetch_article_content("https://example.com")

    assert content.text is None
    assert content.image is None


def test_fetch_article_content_trafilatura(monkeypatch):
    class FakeTrafilaturaMetadata:
        def __init__(self, image):
            self.image = image

    class FakeTrafilatura:
        def extract(self, content, include_comments=False):
            return f"Extracted text from {content}"

        def extract_metadata(self, content):
            return FakeTrafilaturaMetadata("https://example.com/traf_image.jpg")

    fake_traf = FakeTrafilatura()
    monkeypatch.setattr("rss_morning.articles.trafilatura", fake_traf)

    # We need to reload articles module if we relied on global import patch,
    # but here we are patching the imported module in articles.py directly
    # assuming articles was already imported or we use the _install helper.
    # The helper removes rss_morning.articles, so let's use it or just patch standard import.
    # Since _install helper does heavy mocking of newspaper, let's use it to get the module
    # and then patch trafilatura on it.

    articles_module, _ = _install_article_dependencies(monkeypatch)
    monkeypatch.setattr(articles_module, "trafilatura", fake_traf)

    # Test success
    content = articles_module.fetch_article_content(
        "https://example.com/traf", extractor="trafilatura"
    )
    assert content.text == "Extracted text from <html>https://example.com/traf</html>"
    assert content.image == "https://example.com/traf_image.jpg"

    # Test fetch failure
    content_fail = articles_module.fetch_article_content(
        "https://example.com/fail", extractor="trafilatura"
    )
    assert content_fail.text is None
    assert content_fail.image is None


def test_fetch_article_content_defaults_to_newspaper(monkeypatch):
    articles_module, _ = _install_article_dependencies(monkeypatch)

    # Newspaper output
    content = articles_module.fetch_article_content("https://example.com/article")
    assert content.text == "Article body"


def test_truncate_text(monkeypatch):
    articles_module, _ = _install_article_dependencies(monkeypatch)

    class CharacterEncoder:
        @staticmethod
        def encode(value):
            return list(value)

        @staticmethod
        def decode(tokens):
            return "".join(tokens)

    encoder = CharacterEncoder()
    monkeypatch.setattr(articles_module.tiktoken, "get_encoding", lambda _name: encoder)

    original_text = "word " * 1000
    limit_tokens = 300

    truncated = articles_module.truncate_text(original_text, limit=limit_tokens)

    assert len(truncated) < len(original_text)
    assert len(encoder.encode(truncated)) == limit_tokens

    truncated_default = articles_module.truncate_text(original_text)
    assert len(encoder.encode(truncated_default)) == 100


def test_truncate_text_returns_short_input_unchanged(monkeypatch):
    articles_module, _ = _install_article_dependencies(monkeypatch)
    encoder = types.SimpleNamespace(
        encode=lambda value: list(value), decode=lambda tokens: "".join(tokens)
    )
    monkeypatch.setattr(articles_module.tiktoken, "get_encoding", lambda _name: encoder)

    assert articles_module.truncate_text("short", limit=5) == "short"


def test_newspaper_empty_text_and_image(monkeypatch):
    articles_module, _ = _install_article_dependencies(
        monkeypatch, article_text="   ", top_image="   "
    )

    assert articles_module.fetch_article_content("https://example.com") == (
        articles_module.ArticleContent(text=None, image=None)
    )


def test_trafilatura_empty_text_without_metadata(monkeypatch):
    articles_module, _ = _install_article_dependencies(monkeypatch)
    fake = types.SimpleNamespace(
        extract=lambda *_args, **_kwargs: None,
        extract_metadata=lambda _downloaded: None,
    )
    monkeypatch.setattr(articles_module, "trafilatura", fake)

    assert articles_module.fetch_article_content(
        "https://example.com", extractor="trafilatura"
    ) == articles_module.ArticleContent(text=None, image=None)


def test_trafilatura_unexpected_error_is_recoverable(monkeypatch):
    articles_module, _ = _install_article_dependencies(monkeypatch)

    def fail(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(articles_module, "_download_html", fail)

    assert articles_module.fetch_article_content(
        "https://example.com", extractor="trafilatura"
    ) == articles_module.ArticleContent(text=None, image=None)


def test_article_extractors_use_supplied_html_and_redirect_url(monkeypatch):
    articles_module, _ = _install_article_dependencies(
        monkeypatch, top_image="images/lead.jpg"
    )
    calls = []

    class Client:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return types.SimpleNamespace(
                body=b"<html>supplied document</html>",
                final_url="https://redirected.example/news/story",
            )

    content = articles_module.fetch_article_content(
        "https://example.com/start", http_client=Client()
    )

    assert content.text == "Article body"
    assert content.image == "https://redirected.example/news/images/lead.jpg"
    assert calls == [
        (
            "https://example.com/start",
            {
                "accepted_content_types": ("text/html", "application/xhtml+xml"),
                "allow_missing_content_type": True,
            },
        )
    ]
