# RSS Morning

RSS Morning collects the latest items from your curated RSS feeds, optionally summarises them with Google Gemini, and can deliver the results over email via Resend. The project is container-friendly and ships with templates so you can set up your own configuration without exposing secrets.

## Requirements

- Python 3.10+ (3.11 recommended)
- Pip for installing dependencies from `requirements.txt`
- Docker and Docker Compose (optional, for containerised runs)
- Google Gemini API key (optional, required for the `--summary` mode)
- OpenAI API key (optional, required for embedding-based pre-filtering)
- Resend API key (optional, required for sending email)

## Quick Start

1. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy the templates and customise them:
   ```bash
   cp feeds.example.xml feeds.xml
   cp prompt-example.md prompt.md
   cp docker-compose.example.override.yml docker-compose.override.yml
   cp queries.example.txt queries.txt
   ```
3. Edit the new files so they reflect your desired feeds, persona, and runtime options.
4. Export the required secrets (see below).
5. Run the CLI:
   ```bash
   python main.py --feeds-file feeds.xml -n 10 --summary
   ```

## Configuration

- **Feeds (`feeds.xml`)**  
  OPML file listing the RSS sources to poll. Use `feeds.example.xml` as a starting point and replace the sample URLs with feeds that matter to you. Categories are optional but help group the results.

- **Prompt (`prompt.md`)**  
  Gemini system prompt that controls how summaries are generated. `prompt-example.md` documents the expected input/output contract and uses placeholder fields such as `{{STAKEHOLDER_NAME}}`. Adjust the copies to match your audience before running with `--summary`.

- **Docker Compose override**  
  `docker-compose.example.override.yml` shows how to override the container command and inject secrets via environment variables. Copy it to `docker-compose.override.yml` and update the values (`RESEND_API_KEY`, `GOOGLE_API_KEY`, email addresses, etc.) for your deployment.
- **Queries (`queries.txt`)**  
  Embedding topics used by the pre-filter. Copy `queries.example.txt`, customise the entries to the domains you care about, and keep the file beside your configuration. Lines beginning with `#` are treated as comments. If `queries.txt` is missing the application falls back to the example file, but providing your own list ensures the embeddings match your interests.

### Environment Variables

Set these variables in your shell, `.env` file, or Compose override:

- `GOOGLE_API_KEY` – required when using `--summary` (Google Gemini).
- `OPENAI_API_KEY` – required when using the embedding pre-filter or the embedding export CLI.
- `RESEND_API_KEY` – required when emailing results.
- `RESEND_FROM_EMAIL` – default sender address when emailing (can be overridden with `--email-from`).

Any additional CLI parameters (e.g. `--email-to`, `--email-subject`, `--max-age-hours`) can be set in Compose or passed directly when running the CLI.

## Running the CLI

Run directly with Python:
```bash
python main.py \
  --feeds-file feeds.xml \
  --limit 20 \
  --max-age-hours 12 \
  --summary \
  --email-to alerts@example.com
```

CLI highlights:
- Without `--summary`, the command prints raw article JSON.
- With `--summary`, Gemini is called using `prompt.md` and the response is printed and reused for email payloads.
- Add `--pre-filter` to screen articles with the embedding layer. Provide a path (e.g. `--pre-filter query_embeddings.json`) to reuse cached vectors, or supply the flag without an argument to embed the active queries file on the fly.
- Supply `--save-articles path.json` to persist the fetched articles before any filtering or summarisation.
- Reuse a previous snapshot with `--load-articles path.json` to skip RSS fetching (useful for offline iteration or testing summaries).
- Email delivery requires both `--email-to` and a valid Resend configuration.

## Embedding Pre-filter Workflow

The optional embedding pre-filter keeps only articles that are similar to your curated query list.

1. **Curate queries**  
   Copy `queries.example.txt` to `queries.txt` and replace the sample topics with the themes you want to monitor. Blank lines and comments (`# ...`) are ignored.
2. **Pre-compute embeddings (optional but recommended for Docker builds or cold starts)**  
   ```bash
   OPENAI_API_KEY=... python -m rss_morning.prefilter_cli \
     --output query_embeddings.json \
     --queries-file queries.txt
   ```
   This writes `query_embeddings.json` containing the queries, model metadata, and vectors.
3. **Run with the pre-filter**  
   ```bash
   python main.py --feeds-file feeds.xml --pre-filter query_embeddings.json
   ```
   If you omit the path (`--pre-filter` on its own) the application embeds the queries at runtime using `OPENAI_API_KEY`.

The exporter (`python -m rss_morning.prefilter_cli`) shares the same configuration as the main program, so you can change the model, batch size, or threshold with flags such as `--model` or `--threshold`.

### Clustering duplicates

When the pre-filter is active the application further deduplicates near-identical articles using cosine similarity. Related stories are grouped, one “kernel” article survives, and the discarded URLs are surfaced alongside the kernel:

```json
{
  "url": "https://example.com/kernel",
  "other_urls": [
    {"url": "https://example.com/duplicate", "distance": 0.0021}
  ]
}
```

Control the similarity cutoff with `--cluster-threshold` (default `0.84`). Higher values keep more articles, lower values collapse aggressively. Downstream renderers can use the `other_urls` field to show or hide extra context.

## Running with Docker Compose

Build and run in one step:
```bash
docker compose up --build rss-morning
```

Compose automatically merges `docker-compose.override.yml` (if present) so you can version your secrets separately, or keep them out of VCS by copying the example file to an ignored path.

To run the container on demand:
```bash
docker compose run --rm rss-morning -n 5 --feeds-file feeds.xml
```

### Embedding build artefacts

The Dockerfile precomputes query embeddings during the build. Supply the required build arguments so the export step succeeds:

```bash
docker compose build rss-morning \
  --build-arg OPENAI_API_KEY=$OPENAI_API_KEY \
  --build-arg QUERIES_FILE=queries.txt
```

If `QUERIES_FILE` is omitted the build falls back to `queries.example.txt`. At runtime you can continue to pass `--pre-filter query_embeddings.json` (bundled in the image) or rebuild whenever the query list changes.

## Email Delivery

When email settings are present, the app renders both HTML and plain-text bodies using `rss_morning.renderers`. Emails are sent via Resend’s API. Make sure your sender address is verified with Resend before first use to avoid delivery errors.

## Testing

Run the test suite with:
```bash
pytest
```

The tests focus on parsing, rendering, and summarisation helpers. Consider adding integration tests for your customised prompt or feeds if you change core behaviour.

## Troubleshooting

- **Empty output:** Confirm `feeds.xml` contains at least one valid RSS feed and that your network can reach it.
- **Gemini failures:** Check `GOOGLE_API_KEY` and rate limits. The app falls back to raw article JSON if summarisation fails.
- **Email not sent:** Verify that `resend` is installed, your API key is set, and `RESEND_FROM_EMAIL` or `--email-from` points to a verified sender.

## License

RSS Morning is distributed under the Apache License 2.0. See `LICENSE` for the full text.

Happy briefing!
