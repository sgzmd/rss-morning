# RSS Morning

RSS Morning builds a JSON news digest from RSS feeds. It downloads recent feed
entries and article text, can filter articles with embeddings, can ask Gemini for
structured summaries, and can deliver the rendered digest through Resend.

![Example RSS Morning email](static/screenshot.png)

## Requirements

- Python 3.11 (CI) or Python 3.12 (Docker)
- A Google Gemini API key only when summaries are enabled
- An OpenAI API key only when the OpenAI embedding provider is selected
- A Resend API key only when an email recipient is configured

FastEmbed is the default embedding provider and runs locally, but may download its
model on first use.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp feeds.example.xml feeds.xml
cp prompt-example.md prompt.md
cp queries.example.txt queries.txt
cp configs/env.xml.example configs/env.xml
cp configs/config.xml.example configs/config.xml
```

The copied files are ignored by Git because they may contain private feeds,
prompts, and credentials. Edit `configs/config.xml` to enable only the stages you
intend to run. Paths in that file are resolved relative to the config file.

Run the application with:

```bash
python main.py --config configs/config.xml
```

Use `python main.py --help` as the authoritative CLI reference. The supported
runtime options are:

- `--config PATH`
- `--log-level LEVEL`
- `--log-file PATH`
- `--save-articles PATH`
- `--load-articles PATH`
- `--llm-dry-run`
- `--send-email-from-json PATH`

Without summaries, stdout is a JSON list of articles. With summaries enabled, it
is an object containing `summaries` and, when supplied by Gemini, `exec_summary`.
Operational logs are written to stderr.

## Configuration and credentials

The main XML configuration controls feed selection, concurrency, extraction,
filtering, summaries, caching, logging, and email. See
[`configs/config.xml.example`](configs/config.xml.example) for the complete
structure.

Environment values can be stored in the configured environment XML file:

```xml
<environment>
  <variable name="GOOGLE_API_KEY">value</variable>
</environment>
```

Values loaded from this file replace existing process environment values. The
external integrations use:

- `GOOGLE_API_KEY` or `GEMINI_API_KEY` for Gemini summaries
- `OPENAI_API_KEY` for OpenAI embeddings
- `RESEND_API_KEY` for email delivery
- `RESEND_FROM_EMAIL` as the fallback sender address
- `FASTEMBED_CACHE_PATH` for the local FastEmbed model cache

Do not commit populated configuration, environment files, feed lists, prompts,
snapshots, databases, logs, or model caches.

## Safe development workflow

Ordinary tests replace network, model, and email boundaries with deterministic
fakes. An autouse test guard rejects socket connections, so an incompletely faked
boundary fails instead of contacting a real service. The end-to-end test exercises
real XML parsing, CLI assembly, orchestration, and JSON serialization while faking
only feed download, article download, and email delivery. Tests do not require
credentials or live services:

```bash
ruff check .
ruff format --check .
mypy rss_morning main.py
pytest
```

For an offline workflow through the real orchestration and serialization layers,
first capture article data during an intentional network-enabled run:

```bash
python main.py --config configs/config.xml --save-articles articles.json
```

Then replay the ignored snapshot without downloading feeds or article pages:

```bash
python main.py --config configs/config.xml --load-articles articles.json
```

`--llm-dry-run` prepares the Gemini payload and exits before the model request and
before email delivery. It reports preparation at INFO level; the full prompt and
article input are available only at DEBUG level. Use DEBUG logs only where that
data is appropriate. The repository has no automatic real-LLM test: any
production-model smoke test must be invoked deliberately with a bounded input and
an explicitly supplied credential.

## Docker

Build the image and view the CLI without contacting external services:

```bash
docker build -t rss-morning:local .
docker run --rm rss-morning:local --help
```

For Compose, copy `docker-compose.example.override.yml` to the ignored
`docker-compose.override.yml`, create the local files shown in **Setup**, and run:

```bash
docker compose run --rm rss-morning
```

The image runs as an unprivileged user. Compose persists only the FastEmbed cache;
mount any desired database or output location explicitly.

## Architecture

`main.py` delegates configuration and output handling to `rss_morning.cli`. The
pipeline in `rss_morning.runner` calls the feed and article download boundaries,
then optionally invokes the database cache, embedding pre-filter, Gemini summary,
and Resend delivery modules. HTML and text email output is produced by Jinja
templates in `rss_morning/templates`.

Failures fetching an individual feed or article are logged and skipped. Pre-filter
failures keep the original articles, failed Gemini batches are omitted, and email
failures are logged without failing the run. Top-level configuration or pipeline
errors return exit code 1.

## Known compatibility gaps

- `pre-filter/embeddings-path` and `cluster-threshold` are parsed for compatibility,
  but the current runtime filter does not use the precomputed query file or the
  grouping threshold.
- `rss_morning.prefilter_cli` writes a legacy embedding format that the runtime
  does not consume.
- The XML logging `file` value is parsed but only the `--log-file` CLI option
  currently enables file logging.
- The AWS deployment guide may contain historical CLI examples; validate commands
  against `python main.py --help` before use.

## License

RSS Morning is available under the Apache License 2.0. See [LICENSE](LICENSE).
