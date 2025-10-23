import json

import pytest

from rss_morning import summaries


def test_load_system_prompt(tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("You are helpful", encoding="utf-8")

    assert summaries.load_system_prompt(str(prompt_file)) == "You are helpful"


def test_build_summary_input_structure():
    payload = summaries.build_summary_input(
        [
            {
                "title": "Title",
                "url": "https://example.com",
                "summary": "Sum",
                "text": "Content",
                "category": "News",
            }
        ]
    )

    data = json.loads(payload)
    assert data[0]["id"] == "article-1"
    assert data[0]["content"] == "Content"


def test_generate_summary_returns_empty_structure_for_no_articles():
    rendered = summaries.generate_summary([], return_dict=False)
    assert json.loads(rendered)["summaries"] == []


def test_generate_summary_parses_json(monkeypatch):
    def fake_call(system_prompt: str, payload: str) -> str:
        return json.dumps({"summaries": [{"url": "https://example.com"}]})

    monkeypatch.setattr(summaries, "call_gemini", fake_call)

    rendered, parsed = summaries.generate_summary([{"title": "A"}], return_dict=True)

    assert json.loads(rendered)["summaries"]
    assert parsed == {"summaries": [{"url": "https://example.com"}]}


def test_generate_summary_returns_raw_text_when_json_invalid(monkeypatch):
    monkeypatch.setattr(
        summaries, "call_gemini", lambda system_prompt, payload: "not-json"
    )

    rendered = summaries.generate_summary([{"title": "A"}], return_dict=False)

    assert rendered == "not-json"


def test_call_gemini_requires_client(monkeypatch):
    monkeypatch.setattr(summaries, "genai", None)

    with pytest.raises(RuntimeError):
        summaries.call_gemini("prompt", "payload")
