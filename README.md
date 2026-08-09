# RSS Morning

RSS Morning builds a JSON news digest from RSS feeds. It downloads recent feed
entries and article text, can filter articles with embeddings, can ask Gemini or
OpenRouter for structured summaries, and can deliver the rendered digest through
Resend.

![Example RSS Morning email](static/screenshot.png)

## Requirements

- Python 3.11 (CI) or Python 3.12 (Docker)
- A Google Gemini or OpenRouter API key when its summary provider is enabled
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
make production
```

This runs the full configured pipeline, including email delivery when enabled.
Use `CONFIG=path/to/config.xml make production` to select another configuration.
The equivalent direct command is `python main.py --config configs/config.xml`.

Use `python main.py --help` as the authoritative CLI reference. The supported
runtime options are:

- `--config PATH`
- `--log-level LEVEL`
- `--log-file PATH`
- `--save-articles PATH`
- `--load-articles PATH`
- `--llm-dry-run`
- `--send-email-from-json PATH`

## Development checks

Install the pinned development toolchain on top of the runtime dependencies:

```bash
python -m pip install -r requirements-dev.txt
```

`make test` runs the hermetic suite and excludes the opt-in live end-to-end
test. `make coverage` requires 100% statement and branch coverage across
`rss_morning` and `main.py`. Run the complete local/CI gate with:

```bash
make check
```

Without summaries, stdout is a JSON list of articles. With summaries enabled, it
is an object containing `summaries` and, when supplied by the LLM, `exec_summary`.
Operational logs are written to stderr.

## Configuration and credentials

The main XML configuration controls feed selection, concurrency, extraction,
filtering, summaries, caching, logging, and email. See
[`configs/config.xml.example`](configs/config.xml.example) for the complete
structure.

When the database cache is enabled, successful full-text extractions are stored
before output token truncation. Later runs can therefore request a larger output
limit without downloading the page again. Metadata-only extraction failures and
legacy cache rows with no text are treated as misses and retried; the current feed
category always overrides cached metadata. Cache reads are batched before article
workers start, and successful new extractions are written in one transaction after
the workers finish.

Embedding vectors use the separate `embeddings_v2` table. A cache hit requires
matching article content, provider, model, and preprocessing version; vectors are
stored as validated float32 bytes with their dimension. The older model-only
`embeddings` table remains untouched but is not used by the runtime filter.

Token truncation uses one shared `cl100k_base` encoder initialized before article
workers start. If its cache is unavailable offline, the run continues with a
deterministic character-limit approximation and logs one warning without article
content.

Feed and article downloads use a shared bounded HTTP layer with one reusable
session per worker thread. The `<http>` section configures separate connect/read
timeouts, feed/article byte limits, bounded transient GET retries with backoff, and
the maximum concurrent requests to one hostname. See the example configuration
for the conservative defaults. Newspaper and Trafilatura parse the same supplied
HTML, so extractor libraries do not perform additional uncontrolled downloads.
When the database cache is enabled, successful feed responses and their ETag or
Last-Modified validators are stored by the original configured URL. Later runs use
conditional requests; a 304 re-parses the stored bounded body, while a missing or
corrupt body triggers one unconditional recovery request.

Environment values can be stored in the configured environment XML file:

```xml
<environment>
  <variable name="GOOGLE_API_KEY">value</variable>
</environment>
```

Values loaded from this file replace existing process environment values. The
external integrations use:

- `GOOGLE_API_KEY` or `GEMINI_API_KEY` for Gemini summaries
- `OPENROUTER_API_KEY` for OpenRouter summaries
- `OPENAI_API_KEY` for OpenAI embeddings
- `RESEND_API_KEY` for email delivery
- `RESEND_FROM_EMAIL` as the fallback sender address
- `FASTEMBED_CACHE_PATH` for the local FastEmbed model cache
- `TIKTOKEN_CACHE_DIR` for the prewarmed article-tokenizer cache

Do not commit populated configuration, environment files, feed lists, prompts,
snapshots, databases, logs, or model caches.

## Safe development workflow

Ordinary tests replace network, model, and email boundaries with deterministic
fakes. An autouse test guard rejects socket connections, so an incompletely faked
boundary fails instead of contacting a real service. The hermetic end-to-end test
exercises real XML parsing, CLI assembly, orchestration, and JSON serialization
while faking feed download, article download, tokenizer data, and email delivery.
Ordinary tests do not require credentials or live services:

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

`--llm-dry-run` prepares the LLM payload and exits before the model request and
before email delivery or cache access. It reports preparation at INFO level; the
full prompt and article input are available only at DEBUG level. Use DEBUG logs
only where that data is appropriate.

Summaries default to an OpenRouter chain of
`bytedance-seed/seed-2.0-mini`, `z-ai/glm-4.7-flash`, then
`openai/gpt-4o-mini`. Requests require structured JSON output, preserve that
fallback order, prefer price routing, and cap list prices at $0.20/M input and
$0.75/M output tokens. The `<llm>` section can change the chain, routing
(`price`, `throughput`, or `latency`), price caps, batch count/token limits,
timeout, bounded attempts/split depth, and exact-batch caching. Direct Gemini is
available only when explicitly selected.

Failed transient requests are retried with bounded backoff, then multi-article
batches are split up to the configured depth. Returned URLs must exactly match
the submitted batch, required fields must be complete, and only fully validated
responses are cached. Provider model and usage data are retained for internal
cost metrics and do not change stdout JSON.

Candidate quality evaluation uses only the committed synthetic corpus and
requires explicit approval because it makes paid calls:

```bash
RUN_PAID_LLM_EVAL=1 PROMPT=path/to/synthetic-prompt.md make llm-eval
```

## Live end-to-end test

The opt-in live harness exercises the CLI with real RSS feeds, article downloads,
Trafilatura extraction, SQLite caching, local FastEmbed filtering, OpenRouter
structured summaries, and both email renderers. It replaces only the final Resend
API call, then validates the message that would have been delivered.

```bash
make live-e2e
```

The Make target loads `OPENROUTER_API_KEY` from the ignored `env.fish`, enables
the opt-in test, streams DEBUG logs, and requests a verbose, unshortened pytest
traceback. Use `ENV_FISH=path/to/file.fish make live-e2e` if the Fish environment
file has a different name.

The bounded live-test defaults are `BAAI/bge-small-en-v1.5` for local embeddings
and `bytedance-seed/seed-2.0-mini` for summaries. Override the summary model
without editing the harness:

```fish
set -lx OPENROUTER_E2E_MODEL openai/gpt-oss-20b
make live-e2e
```

Ordinary test runs skip the live test unless `RUN_LIVE_E2E=1` is set. Individual
unavailable feeds remain recoverable through the application's normal behavior;
if every feed or an essential model endpoint is unavailable, the live test fails.
The live command streams application DEBUG logs as work happens and requests a
verbose, unshortened pytest traceback. DEBUG output includes prompt and article
content, so run it only in a terminal or CI log suitable for that data.

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
then optionally invokes the database cache, embedding pre-filter, configured LLM
summary, and Resend delivery modules. HTML and text email output is produced by
Jinja templates in `rss_morning/templates`.

Failures fetching an individual feed or article are logged and skipped. Pre-filter
failures keep the original articles, failed LLM batches are omitted, and email
failures are logged without failing the run. Top-level configuration or pipeline
errors return exit code 1.

Configuration parsing is strict and completes before feeds, databases, worker
pools, or model clients are opened. Numeric limits must be in range, booleans
must be exactly `true` or `false`, and provider/model/enumeration values must be
supported and nonempty. Error messages name the invalid XML path and safe value.
Enabling summaries requires a nonempty prompt file. Configuring an email recipient
also requires either `<email><from>` or `RESEND_FROM_EMAIL` (including values
loaded from the configured environment file).

The pre-filter may load a compatible version-2 query-vector file from
`pre-filter/embeddings-path`; stale or corrupt files are ignored and recomputed.
Within each matched category, candidates are ordered by relevance and greedily
clustered using `cluster-threshold` as cosine similarity in the inclusive `[0, 1]`
range, with `max-cluster-size` defaulting to five. Only representatives proceed to summarization, while duplicate source URLs
and their distances remain in the representative's `other_urls` field.

`pre-filter/mode` defaults to `full-text`, preserving the original behavior of
downloading every selected page before filtering. Opt-in `metadata-first` embeds
uncached feed titles and summaries first (while using cached full text when
available), retains up to `candidate-multiplier * max_cluster_size` candidates per
category, and downloads only those pages before normal full-text scoring. If the
first stage fails, the run falls back to the full-text path.

Feedparser date tuples are interpreted as UTC directly, independent of the
machine's local timezone. Entries without a date use the minimum UTC timestamp
and sort last.

## Known compatibility gaps

- The XML logging `file` value is parsed but only the `--log-file` CLI option
  currently enables file logging.
- The AWS deployment guide may contain historical CLI examples; validate commands
  against `python main.py --help` before use.

## License

RSS Morning is available under the Apache License 2.0. See [LICENSE](LICENSE).
