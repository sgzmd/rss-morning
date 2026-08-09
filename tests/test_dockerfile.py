"""Static container contracts that can be checked without Docker or networking."""

from pathlib import Path


def test_dockerfile_prewarms_readable_tokenizer_cache_before_runtime_user():
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "TIKTOKEN_CACHE_DIR=/app/data/tiktoken_cache" in dockerfile
    prewarm = 'tiktoken.get_encoding("cl100k_base")'
    assert prewarm in dockerfile
    assert dockerfile.index(prewarm) < dockerfile.index("USER appuser")
