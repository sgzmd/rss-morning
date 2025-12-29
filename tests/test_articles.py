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


def test_fetch_article_content_trafilatura(monkeypatch):
    class FakeTrafilaturaMetadata:
        def __init__(self, image):
            self.image = image

    class FakeTrafilatura:
        def fetch_url(self, url):
            if "fail" in url:
                return None
            return f"<html>{url}</html>"

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
    # "x" encodes to 1 token in cl100k_base usually, but let's just assert on behavior.
    # Actually, "x" might be part of a larger token if repeated, or single tokens.
    # 'x' is 1 token. 'x'*1050 should be 1050 tokens.
    # Let's use a sentence to be more realistic? Or just rely on token count check.
    # To properly test, we need to know the tokenization.
    # "hello world " is 2 tokens? No.
    # Let's import tiktoken here to verify expected behavior or just trust the mock/implementation?
    # We should probably not mock tiktoken in unit tests unless we want to avoid dependency,
    # but we added it as dependency.

    # We'll use a string that we know will be truncated.
    original_text = "word " * 1000
    limit_tokens = 300

    truncated = articles_module.truncate_text(original_text, limit=limit_tokens)

    # Verify it is shorter than original
    assert len(truncated) < len(original_text)

    # Verify it's roughly the right size (word + space is often 1-2 tokens)
    # Let's actually verify using the same encoder if possible, or just check basic property.
    # Ideally we should see if we can import tiktoken in the test environment if not mocked out
    # But since we are modifying articles.py to import it, we don't mock it out in _install_article_dependencies
    # unless we explicitly do so?
    # _install_article_dependencies only mocks 'newspaper'. It imports rss_morning.articles.
    # So rss_morning.articles will import real tiktoken.

    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    assert len(enc.encode(truncated)) == limit_tokens

    # Verify default is 100
    truncated_default = articles_module.truncate_text(original_text)
    assert len(enc.encode(truncated_default)) == 100
