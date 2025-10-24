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

        def download(self):
            if download_error:
                raise download_error

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
    return importlib.import_module("rss_morning.articles"), FakeArticleException


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


def test_truncate_text(monkeypatch):
    articles_module, _ = _install_article_dependencies(monkeypatch)
    text = "x" * 1050

    truncated = articles_module.truncate_text(text, limit=100)

    assert len(truncated) == 100
