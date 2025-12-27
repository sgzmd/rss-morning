"""Integration with Gemini for article summaries."""

from __future__ import annotations

import json
import logging
from typing import Optional, Tuple, List, Dict

from bs4 import BeautifulSoup

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - optional dependency
    genai = None
    types = None

from .config import TopicCluster

logger = logging.getLogger(__name__)


def sanitize_html(text: str) -> str:
    """Remove HTML tags from text."""
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text()


def generate_summary(
    articles: list[dict],
    system_prompt: str,  # We might ignore this or append it, the user provided a specific prompt template
    return_dict: bool = False,
    batch_size: int = 20,  # per topic batching if needed
    api_key: Optional[str] = None,
    topics: Optional[List[TopicCluster]] = None,
) -> str | Tuple[str, Optional[dict]]:
    """Generate summary JSON for a list of articles, grouped by topic."""

    if not articles:
        empty = {"summaries": []}
        return (
            (json.dumps(empty, ensure_ascii=False), empty)
            if return_dict
            else json.dumps(empty, ensure_ascii=False)
        )

    if genai is None or types is None:
        raise RuntimeError(
            "google-genai package is required for --summary but is not installed."
        )

    client = genai.Client(api_key=api_key)
    model = "gemini-flash-latest"

    results_list = []

    effective_topics = topics or []

    # Group articles by topic
    articles_by_topic: Dict[str, List[dict]] = {
        topic.name: [] for topic in effective_topics
    }

    # Also handle articles that might not match any known topic (if prefilter was skipped or weirdness)
    # But usually they should have a category matches topic.name if they came from prefilter.
    # If prefilter was NOT run, we might need to rely on existing categories?
    # The user instruction implies this runs after prefilter.
    # If run without prefilter, we might just put them in "Unclassified" or skip?
    # Let's assume they have categories.

    for article in articles:
        cat = article.get("category")
        if cat in articles_by_topic:
            articles_by_topic[cat].append(article)
        else:
            # Try to find if it matches any topic ID or something, else "Other"
            found = False
            for t in effective_topics:
                if t.id == cat:
                    articles_by_topic[t.name].append(article)
                    found = True
                    break
            if not found:
                # maybe put in a default bucket?
                pass

    for topic in effective_topics:
        candidate_articles = articles_by_topic[topic.name]
        if not candidate_articles:
            continue

        logger.info(
            f"Summarizing topic '{topic.name}' with {len(candidate_articles)} articles."
        )

        # Prepare context
        titles_lines = []
        for i, row in enumerate(candidate_articles):
            # Safe truncation
            content_snippet = (row.get("text") or "")[:200]
            # ID is just index in this list for reference
            titles_lines.append(
                f"- [ID {i}] {row.get('title')} || {content_snippet}..."
            )

        titles_text = "\n".join(titles_lines)

        # The Prompt from the user request
        prompt_text = f"""
        Role: Senior Intelligence Analyst.
        Task: You are analyzing a stream of security news candidates for Topic Cluster: "{topic.name}".
        
        Input Data:
        {titles_text}

        Instructions:
        1. **Filter:** Identify which of these items genuinely belong to "{topic.name}". Ignore irrelevant items that might have slipped through vector search.
        2. **Summarize:** Write a briefing for the valid items.
        
        Guidelines for "Tone Calibration":
        1. **De-sensationalize:** Ignore clickbait (e.g., "Catastrophic", "Nightmare") unless technical facts support it.
        2. **Neutrality:** If an issue is "business as usual" (routine patch, minor bug), describe it calmly.
        3. **Severity:** Distinguish between "Active Exploitation" (High) and "Theoretical" (Low).
        4. **Source Attribution:** For each bullet point, include the source article title and URL in the format: "(source: [Title](URL))".
        5. Aim to have between 3 to 7 bullet points.

        Output Format (JSON):
        {{
            "valid_count": <int>,
            "key_threats_summary": [
                "<Bullet point (Neutral & Precise)> (source: [Title](URL))",
                "<Bullet point (Neutral & Precise)> (source: [Title](URL))",
                "<Bullet point (Neutral & Precise)> (source: [Title](URL))"
            ],
            "related_articles": [
                {{ "title": text, "url": text }}
            ]
        }}
        """

        try:
            # We use the new SDK's JSON mode
            response = client.models.generate_content(
                model=model,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "valid_count": types.Schema(type=types.Type.INTEGER),
                            "key_threats_summary": types.Schema(
                                type=types.Type.ARRAY,
                                items=types.Schema(type=types.Type.STRING),
                            ),
                            # Adding this to validly link back
                            "valid_article_ids": types.Schema(
                                type=types.Type.ARRAY,
                                items=types.Schema(type=types.Type.INTEGER),
                                description="List of IDs (from Input Data) that were included in this summary.",
                            ),
                        },
                        required=["valid_count", "key_threats_summary"],
                    ),
                ),
            )

            data = json.loads(response.text)

            # Map back articles if we can
            valid_ids = data.get("valid_article_ids", [])
            valid_articles_list = []
            if valid_ids:
                for idx in valid_ids:
                    if 0 <= idx < len(candidate_articles):
                        valid_articles_list.append(candidate_articles[idx])
            else:
                # If LLM didn't return IDs, maybe just assume all inputs?
                # Or just don't show links?
                # Let's include all candidates if filtered count is close?
                # Safest is to attach all candidates as "sources" but maybe mark them?
                # Actually, if I want to show links in the email, I need them.
                # Let's assume we attach all candidate_articles for now as potential sources
                # or just the ones valid.
                valid_articles_list = candidate_articles

            summary_item = {
                "category": topic.name,  # used for grouping in template
                "topic": topic.name,
                "valid_count": data.get("valid_count"),
                "key_threats": data.get("key_threats_summary"),
                "summary": {  # Structure expected by template roughly?
                    "title": f"Briefing: {topic.name}",
                    "what": "\n".join(data.get("key_threats_summary") or []),
                    "so-what": "",  # Not provided by this prompt
                    "now-what": "",  # Not provided by this prompt
                },
                # We can put articles here to list them below the summary
                "articles": valid_articles_list,
                "image": valid_articles_list[0].get("image")
                if valid_articles_list
                else None,
            }
            results_list.append(summary_item)

        except Exception as e:
            logger.error(f"Error processing cluster {topic.name}: {e}")

    final_obj = {"summaries": results_list}
    rendered = json.dumps(final_obj, ensure_ascii=False, indent=2)

    if return_dict:
        return rendered, final_obj
    return rendered
