# RSS Morning agent guide

## Purpose

RSS Morning makes a news digest from RSS feeds. It fetches recent posts, extracts article text, optionally filters items with embeddings, optionally asks Gemini for structured summaries, prints JSON, and can send the result through Resend.

Keep this file aligned with the code. `README.md` contains useful background but still shows an older command-line interface. For runtime behavior, trust the code, tests, and `python main.py --help` first.

## Runtime flow

```text
main.py
  -> cli.py: load XML config, env values, and CLI overrides
  -> runner.py: coordinate the run
     -> config.py: read OPML feeds
     -> feeds.py: fetch feeds and select recent entries
     -> articles.py: fetch, extract, and trim article pages
     -> db.py: optionally cache articles
     -> prefilter.py: optionally score and group articles
        -> embeddings.py: FastEmbed or OpenAI vectors
        -> db.py: optionally cache article vectors
     -> summaries.py: optionally ask Gemini for JSON summaries
     -> emailing.py: optionally send rendered templates through Resend
  -> cli.py: print final JSON
```

Feed downloads and article downloads use separate thread pools. `concurrency` controls both pools. Feed entries are limited per feed, merged, sorted by publication time, and deduplicated by URL before article pages are downloaded.

One failed feed or article is logged and skipped. Pre-filter errors fail open and keep the original articles. Failed Gemini batches are logged and omitted. Email failures are logged and do not fail the run. Errors in top-level configuration or orchestration return exit code 1.

## Main files

- `main.py`: thin executable entry point; adds `--log-level DEBUG` when no log level is supplied.
- `rss_morning/cli.py`: CLI, configuration assembly, logging, output, and exit codes.
- `rss_morning/config.py`: main XML, env XML, and OPML parsing.
- `rss_morning/runner.py`: pipeline, concurrency, snapshots, sorting, and optional stages.
- `rss_morning/feeds.py`: RSS download, date conversion, summary cleanup, and per-feed selection.
- `rss_morning/articles.py`: Newspaper and Trafilatura extractors and token truncation.
- `rss_morning/prefilter.py`: query loading, centroid scoring, category assignment, and grouping metadata.
- `rss_morning/embeddings.py`: FastEmbed and OpenAI embedding backends.
- `rss_morning/summaries.py`: Gemini batching, response schema, and text cleanup.
- `rss_morning/db.py`: SQLAlchemy article and embedding cache.
- `rss_morning/emailing.py`: Resend integration.
- `rss_morning/renderers.py`, `rss_morning/templating.py`, `rss_morning/templates/`: HTML and text email rendering.
- `rss_morning/prefilter_cli.py`: legacy query-embedding export command; see known gaps below.
- `tests/`: unit tests. External network and API work should be replaced with fakes.

## Setup and checks

CI uses Python 3.11. The Docker image uses Python 3.12. Use either unless a dependency proves otherwise.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py --help
```

Before handing off a change, run checks that match its scope:

```bash
ruff check .
ruff format --check .
pytest
```

The pre-commit config applies Ruff fixes, Ruff formatting, and pytest. Its local pytest path is `./venv/bin/python`, while the common environment directory in this checkout is `.venv`; do not assume the hook works without checking the interpreter path.

Tests use `pytest.ini`, set the project root on `PYTHONPATH`, and should not need live RSS, Gemini, OpenAI, or Resend access. Add or update tests when changing configuration parsing, pipeline stages, output fields, templates, or failure behavior.

## Running the app

The current CLI is config-first:

```bash
python main.py --config configs/config.xml
```

Supported CLI options are:

- `--config PATH`
- `--log-level LEVEL`
- `--log-file PATH`
- `--save-articles PATH`
- `--load-articles PATH`
- `--llm-dry-run`
- `--send-email-from-json PATH`

Most older flags shown in `README.md` no longer exist. Do not add them to scripts unless the CLI is deliberately restored.

Use `--save-articles` to capture fetched article objects before filtering and summarizing. Use `--load-articles` to replay that JSON without fetching feeds or pages. This is the preferred loop for prompt and filter work. `--llm-dry-run` builds and logs the Gemini input, returns `{"dry_run": true}`, and stops before email.

## Configuration contract

The main XML file defaults to `configs/config.xml`. Relative paths in it resolve from the directory containing that XML file.

Recognized settings are:

- `<feeds>`: required OPML path.
- `<env>`: optional env XML path.
- `<limit>`: maximum entries selected from each feed; default `10`.
- `<max-age-hours>`: optional positive age cutoff.
- `<summary>`: `true` or `false`; default `false`.
- `<max-article-length>`: maximum article text tokens; default `100`.
- `<extractor>`: `newspaper` or `trafilatura`; unknown values currently fall back to Newspaper.
- `<concurrency>`: worker count for feed and article pools; default `10`.
- `<prompt file="..."/>`: prompt file. It is required when summaries are enabled. Inline prompt text is not supported.
- `<pre-filter>`: `enabled`, optional `queries-file`, optional `embeddings-path`, and `cluster-threshold`.
- `<embeddings>`: `provider` and `model`. `fastembed` is the default provider. Any provider value other than `fastembed` selects OpenAI.
- `<database>`: `enabled` and a SQLAlchemy `connection-string`.
- `<email>`: `to`, `from`, and `subject`.
- `<logging>`: `level` and `file`.

The env XML format is:

```xml
<environment>
  <variable name="GOOGLE_API_KEY">value</variable>
</environment>
```

Values from this file overwrite variables already present in the process environment.

The feed file is OPML. Nested non-feed outlines provide categories. Feed outlines require `type="rss"` and `xmlUrl`. Entries without a URL or title are skipped. Entries without a date sort as the oldest possible date.

Query files can be JSON or plain text:

- JSON maps category names to lists of query strings.
- A JSON list or plain-text file becomes one `General` category.
- Blank text lines and lines starting with `#` are ignored.
- Without an explicit path, lookup checks `configs/queries.json`, `queries.json`, `queries.txt`, then `queries.example.txt`.

Do not use `configs/config.xml.example` as a working file without fixing it. It currently has a duplicate `<email>` opening tag and an inline prompt, while the parser requires a `file` attribute.

## External services and secrets

- Gemini summaries use `GOOGLE_API_KEY` or `GEMINI_API_KEY` and the hard-coded model `gemini-flash-latest`.
- OpenAI embeddings use `OPENAI_API_KEY` when the provider is not `fastembed`.
- Resend uses `RESEND_API_KEY`. The sender comes from the XML email `from` value or `RESEND_FROM_EMAIL`.
- FastEmbed runs locally but may download its model on first use. `FASTEMBED_CACHE_PATH` controls its cache in Docker.

Never commit real config files, env XML, API keys, feed lists, prompts, logs, databases, snapshots, model caches, or generated output. Most are already ignored. Treat ignored files as user data: inspect only when needed, do not rewrite them casually, and never print secrets.

Security warning: `summaries.py` currently logs the full Gemini API key at INFO level because it logs `api_key[:]`. Do not repeat this pattern. Avoid running summary mode with logs that may be shared until this is fixed.

## Data contracts

Before summarization, an article is a dictionary with these common fields:

```text
url        required source URL
category   OPML category, later replaced by pre-filter category if enabled
title      feed title
summary    cleaned feed summary, possibly empty
published  ISO timestamp or null
text       extracted and token-trimmed article text, possibly absent
image      absolute lead image URL, possibly absent
```

The pre-filter may add `prefilter_score`, `prefilter_match`, and `other_urls`. It embeds `title + summary + text`, compares the vector with each query-category centroid, applies the fixed default threshold `0.5`, and keeps up to five articles per matching category. The best article in each category lists the other retained URLs and cosine distances.

Without summaries, stdout is a JSON list of article dictionaries.

With summaries, stdout is an object with `summaries` and, when Gemini supplies it, `exec_summary`. Each summary item contains `url`, `category`, and a nested `summary` with `title`, `rank-reasoning`, `what`, `so-what`, and `now-what`. Gemini output is stripped of HTML. The runner restores a source image when the summary item has none.

Email templates accept both raw article lists and summarized objects. Markdown in summary fields is rendered and sanitized before it enters the HTML email.

## Database behavior

When enabled, SQLAlchemy creates `articles` and `embeddings` tables. Articles are keyed by URL. Embeddings are keyed by URL plus the configured model string. Vectors are JSON encoded into binary columns.

A cached article supplies its saved title, text, image, summary, and publication date, but uses the category from the current feed entry. There is no cache expiry. Changing extraction behavior does not refresh existing rows automatically.

## Known gaps and misleading settings

Do not silently build new behavior around these settings; either preserve current behavior or fix it with tests and documentation:

- `pre-filter/embeddings-path` is parsed and passed through, but the current filter ignores precomputed query embeddings.
- `cluster-threshold` is parsed and passed to the filter, but current grouping does not use it.
- `rss_morning.prefilter_cli` exports a legacy format that the runtime does not consume.
- `<logging><file>` is parsed, but `cli.py` ignores it. Only `--log-file` currently enables file logging. `RSS_MORNING_LOG_STDOUT=1` disables file logging, despite its name; the default `StreamHandler` writes to stderr.
- `configs/config.xml.example` is malformed and does not match the prompt parser.
- The README, Compose override example, and AWS guide may contain old CLI usage. Check commands against `python main.py --help`.
- The pre-commit pytest hook refers to `./venv`, not `.venv`.

## Change rules

- Preserve the JSON shapes unless the task explicitly changes the public contract.
- Keep partial-failure behavior deliberate. Do not turn a recoverable feed or API failure into a full-run crash without a clear reason.
- Keep network, model, email, and database boundaries easy to fake in tests.
- Resolve config-relative paths through `config.py`; do not depend on the caller's working directory.
- Do not log prompts, article bodies, credentials, connection strings, or full third-party responses at INFO level.
- Keep HTML escaped or sanitized. The existing Jinja environment autoescapes HTML, and Markdown output is cleaned with Bleach.
- Preserve unrelated local and ignored files. This repository commonly contains private configs, feeds, logs, databases, caches, and output snapshots.
- Prefer focused changes. If a known gap is outside the requested work, note it instead of folding a broad cleanup into the patch.
