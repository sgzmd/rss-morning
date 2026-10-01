from unittest.mock import patch

from rss_morning.articles import fetch_article_content, truncate_text


class FakeTrafilaturaMetadata:
    def __init__(self, image):
        self.image = image


def test_fetch_article_content_success():
    with (
        patch("trafilatura.fetch_url") as mock_fetch,
        patch("trafilatura.extract") as mock_extract,
        patch("trafilatura.extract_metadata") as mock_meta,
    ):
        mock_fetch.return_value = "<html>mock html</html>"
        mock_extract.return_value = "Extracted article body"
        mock_meta.return_value = FakeTrafilaturaMetadata("https://example.com/lead.jpg")

        content = fetch_article_content("https://example.com/story")

        assert content.text == "Extracted article body"
        assert content.image == "https://example.com/lead.jpg"


def test_fetch_article_content_download_failure():
    with patch("trafilatura.fetch_url", return_value=None):
        content = fetch_article_content("https://example.com/story")
        assert content.text is None
        assert content.image is None


def test_fetch_article_content_exception_handled():
    with patch("trafilatura.fetch_url", side_effect=RuntimeError("Network explosion")):
        content = fetch_article_content("https://example.com/story")
        assert content.text is None
        assert content.image is None


def test_truncate_text_within_limit():
    short_text = "This is a short sentence."
    assert truncate_text(short_text, limit=100) == short_text


def test_truncate_text_exceeds_limit():
    text = "The quick brown fox jumps over the lazy dog."
    truncated = truncate_text(text, limit=20)
    assert len(truncated) <= 20
    # Breaks cleanly at word boundary
    assert truncated == "The quick brown fox"


def test_truncate_text_no_spaces():
    long_word = "A" * 50
    truncated = truncate_text(long_word, limit=20)
    assert len(truncated) == 20
    assert truncated == "A" * 20


def test_truncate_text_empty_or_none():
    assert truncate_text("", limit=100) == ""
    assert truncate_text(None, limit=100) is None
