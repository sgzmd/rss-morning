from unittest.mock import patch
from rss_morning.articles import fetch_article_content, ArticleContent


@patch("rss_morning.articles._fetch_with_newspaper")
def test_fetch_article_content_fixes_relative_image_url(mock_fetch):
    # Setup the mock to return a relative image URL
    mock_fetch.return_value = ArticleContent(
        text="Some text", image="/static/images/logo.png"
    )

    url = "https://example.com/article/123"
    content = fetch_article_content(url, extractor="newspaper")

    # The image URL should be joined with the base URL
    assert content.image == "https://example.com/static/images/logo.png"


@patch("rss_morning.articles._fetch_with_newspaper")
def test_fetch_article_content_leaves_absolute_url_untouched(mock_fetch):
    # Setup the mock to return an absolute image URL
    mock_fetch.return_value = ArticleContent(
        text="Some text", image="https://cdn.example.com/images/logo.png"
    )

    url = "https://example.com/article/123"
    content = fetch_article_content(url, extractor="newspaper")

    assert content.image == "https://cdn.example.com/images/logo.png"
