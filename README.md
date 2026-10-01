# RSS Morning

RSS Morning is a focused, deterministic morning briefing generator. It reads RSS/Atom feeds, filters for relevance using cheap metadata, extracts full article content only for plausible candidates, synthesises an editorial edition via a single OpenRouter LLM call, and renders a structured briefing for email delivery or stdout.

<img src="static/screenshot.png" style="max-width: 500px"/>

## Architecture

```text
RSS / Atom feeds (OPML)
    │
    ▼
Normalise + exact URL deduplication
    │
    ▼
Jev classification on cheap RSS metadata (TypeSafe System One)
    │
    ▼
Fetch full text only for plausible articles (Trafilatura)
    │
    ▼
Single OpenRouter LLM call acting as edition editor
    │
    ▼
Deterministic digest schema (overview, attention, watch)
    │
    ▼
Jinja HTML & plaintext rendering
    │
    ▼
Email (Resend) or stdout
```

### Separation of Concerns

- **Standard Python**: Exact deterministic operations (feed ingestion, URL deduplication, HTTP extraction, text truncation).
- **Jev (TypeSafe System One)**: Bounded semantic classification on cheap metadata before full-text fetch (`relevance_probability`, `primary_area`).
- **OpenRouter LLM**: Single edition editor call for story clustering, importance assessment, and prose synthesis.
- **Jinja**: Presentation rendering without editorial logic.

## Credentials

The system requires at most three credentials configured in `.env` or process environment:

```bash
# TypeSafe System One (Jev classification)
TYPESAFE_API_KEY=your_typesafe_key

# OpenRouter (All generative LLM synthesis)
OPENROUTER_API_KEY=your_openrouter_key

# Resend (Optional: for sending email briefings)
RESEND_API_KEY=your_resend_key
```

No direct vendor SDKs (Gemini, OpenAI, Anthropic) are used. All generative model access is routed exclusively through OpenRouter.

## Quick Start

1. **Install dependencies:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure:**
   ```bash
   cp .env.example .env
   cp configs/config.toml.example configs/config.toml
   cp feeds.example.xml feeds.xml
   ```
   - Add your API keys to `.env`.
   - Customise your feed subscriptions in `feeds.xml` (standard OPML format).
   - Customise options in `configs/config.toml`.

3. **Run:**
   ```bash
   # Run with default configuration
   python main.py

   # Dry run LLM (formats prompt and schema without calling the API)
   python main.py --llm-dry-run

   # Debugging: save/load article snapshots to avoid re-fetching
   python main.py --save-articles snapshot.json
   python main.py --load-articles snapshot.json --llm-dry-run
   ```

## Configuration (`configs/config.toml`)

Configuration is managed via standard TOML:

```toml
feeds = "feeds.xml"
limit = 10
max_age_hours = 24.0
summary = true
concurrency = 10
max_article_length = 250
prompt_file = "prompt.md"

[classification]
enabled = true
model = "jev-latest"
threshold = 0.50

[email]
to = "user@example.com"
from = "mailer@example.com"
subject = "Morning RSS Digest"

[logging]
level = "INFO"
file = "logs/rss-morning.log"

[llm]
model = "anthropic/claude-3.5-haiku"
```

### Feeds (`feeds.xml`)

Feeds are managed in standard OPML (Outline Processor Markup Language). OPML is used because it is the universal import/export format across feed readers (NetNewsWire, Feedly, Inoreader).

## Docker

Run via Docker Compose:

```bash
docker compose up --build
```

## Testing

Run unit tests hermetically without live network calls:

```bash
pytest
```

## License

RSS Morning is distributed under the Apache License 2.0. See `LICENSE` for details.
