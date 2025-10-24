# RSS Morning

Some of us just want to sip the first coffee of the day and know whether the world is on fire. Others (hi) are on call for the industry gossip mill and still want to keep the caffeine sacred. RSS Morning is the homebrew rig I built so I can stay in the loop without doomscrolling twenty browser tabs. It slurps the headlines from your carefully curated feeds, bins the junk with embeddings, writes summaries that sound like a thoughtful teammate, and — if you want — emails the whole thing to you before your coffee is cold.

## What It Actually Does

- Pulls the latest items from an OPML feed list and runs them through Readability so you get clean article text.
- Scores every article against your “what I care about” queries using OpenAI embeddings; trash gets tossed, gems survive.
- Hands the survivors to Google Gemini for summaries in the “What? So What? Now What?” style when `--summary` is on.
- Packages the result as JSON, console output, or a Resend email (HTML + plain text).
- Ships with Docker bits, templates, and enough knobs that you can run it in the cloud, on a Raspberry Pi, or on the laptop that lives in your kitchen.

## Gear Checklist

- Google Gemini API key when you plan to summarise (which is kind the whole point)
- OpenAI API key if you want the embedding pre-filter (or to export embeddings ahead of time) - and trust me, you really do want it, otherwise you'll be reading same news story 10 times.
- Resend API key unless reading JSON from console is your thing
- Python 3.10+ and the usual stuff
- Docker and Docker Compose if you want to containerise (makes no difference if you are running locally, but kinda nicer if you want to ship stuff to your VPS and run on cron)

## First Brew (a.k.a. Quick Start)

1. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy the templates and give them a personal touch:
   ```bash
   cp feeds.example.xml feeds.xml
   cp prompt-example.md prompt.md
   cp docker-compose.example.override.yml docker-compose.override.yml
   cp queries.example.txt queries.txt
   ```
3. Edit those copies so they describe your feeds, tone of voice, and runtime options.
4. Export the secrets your run needs (see “Secret Sauce” below).
5. (Optional but highly recommended) Pre-brew the query embeddings so future runs stay fast and you don’t re-pay for cosine math every time:
    ```bash
    OPENAI_API_KEY=... python -m rss_morning.prefilter_cli \
      --output query_embeddings.json \
      --queries-file queries.txt
    ```
   Do it once and the CLI can reuse `query_embeddings.json` instead of embedding on each run.
6. Fire it up:
    ```bash
    python main.py \
      --feeds-file feeds.xml \
      -n 10 \
      --summary \
      --pre-filter ./query_embeddings.json
    ```

## Tune The Inputs

### Feeds (`feeds.xml`)
Drop your favourite RSS sources into an OPML file. The provided `feeds.example.xml` is just scaffolding (albeit usable) — swap the sample URLs for your real feeds. Categories are optional but help group the output.

### Prompt (`prompt.md`)
This is the Gemini system prompt for summaries. `prompt-example.md` spells out the contract and uses placeholders like `{{STAKEHOLDER_NAME}}`. Copy it, tweak the tone, and make sure it matches who is reading your briefing before you run with `--summary`.

### Queries (`queries.txt`)
These are the “what I care about” statements the embedding pre-filter uses. Start from `queries.example.txt`, keep the lines you like, add the topics you actually track, and leave comments with `#` as breadcrumbs. If `queries.txt` is missing, the app falls back to the example file—but supplying your own makes the cosine similarity actually meaningful.

## Secret Sauce (Environment Variables)

Set these in your shell, a `.env`, or via Compose:

- `GOOGLE_API_KEY` – mandatory when summaries are involved.
- `OPENAI_API_KEY` – needed for embedding the queries or exporting cached vectors.
- `RESEND_API_KEY` – unlocks email delivery.
- `RESEND_FROM_EMAIL` – default “from” address for mail (override with `--email-from` if needed).

`docker-compose.example.override.yml` shows how to inject secrets and override the container command. Copy it to `docker-compose.override.yml`, fill in values such as `RESEND_API_KEY`, `GOOGLE_API_KEY`, `RESEND_FROM_EMAIL`, and your destinations, and you’re set for Compose-driven runs.

Anything else—`--email-to`, `--email-subject`, `--max-age-hours`, etc.—can ride along as CLI flags or in your Compose override.

## Drive It From The CLI

Go direct:
```bash
python main.py \
  --feeds-file feeds.xml \
  --limit 20 \
  --max-age-hours 12 \
  --summary \
  --email-to alerts@example.com
```

Highlights:
- Skip `--summary` to get raw JSON for each article.
- With `--summary`, Gemini uses your prompt, and the output is reused for email payloads.
- Add `--pre-filter` to enable the embedding screen. Point it at a cache (e.g. `--pre-filter query_embeddings.json`) or leave it flag-only to embed queries on the fly.
- `--save-articles path.json` stores the fetched articles before filters or summaries touch them.
- `--load-articles path.json` replays a previous fetch so you can iterate offline or tweak prompts without hammering RSS.
- Emailing requires `--email-to` plus a working Resend setup.

## Embeddings: The Pre-Filter Loop

This optional (with `--pre-filter`) stage keeps the noise down by comparing articles against your query list.

1. **Curate queries**  
   Copy `queries.example.txt` to `queries.txt` and rewrite the topics so they match what you care about. Blank lines and lines starting with `#` are ignored.
2. **Pre-compute embeddings (optional, handy for Docker builds and cold starts)**  
   ```bash
   OPENAI_API_KEY=... python -m rss_morning.prefilter_cli \
     --output query_embeddings.json \
     --queries-file queries.txt
   ```
   That produces `query_embeddings.json` with the queries, model metadata, and vectors.
3. **Run with the pre-filter**  
   ```bash
   python main.py --feeds-file feeds.xml --pre-filter query_embeddings.json
   ```
   Omit the path (`--pre-filter` on its own) if you’d rather embed at runtime using `OPENAI_API_KEY`.

`python -m rss_morning.prefilter_cli` accepts the same flags as the main CLI—tweak the model, batch size, or threshold with `--model`, `--batch-size`, `--threshold`, etc.

### Clustering Duplicates

Once the pre-filter is live, the app also merges near-identical stories using cosine similarity. One “kernel” article survives, and the clones are listed under `other_urls`:

```json
{
  "url": "https://example.com/kernel",
  "other_urls": [
    {"url": "https://example.com/duplicate", "distance": 0.0021}
  ]
}
```

Control how aggressive that merge is with `--cluster-threshold` (default `0.8` - works reasonably well for me). Raise it to keep more versions, lower it to collapse clusters harder. Renderers can choose whether to surface those `other_urls`.

## Docker, Because Of Course

Build and run in one shot:
```bash
docker compose up --build rss-morning
```

Compose automatically merges `docker-compose.override.yml`, so you can keep secrets out of Git or stash them in an ignored copy.

Need a one-off run?
```bash
docker compose run --rm rss-morning -n 5 --feeds-file feeds.xml
```

## Email Delivery

Flip on email settings and the app renders both HTML and text bodies via `rss_morning.renderers`, then hands them to Resend. Make sure your sending address is verified with Resend so the first run doesn’t vanish into the void.

## Testing

```bash
pytest
```

The existing suite leans on parsing, rendering, and summarisation helpers. If you heavily customise prompts or feed handling, consider layering in your own integration checks.

## Troubleshooting

- **Empty output:** Double-check `feeds.xml` for valid RSS URLs and make sure your network can reach them.
- **Gemini complaints:** Inspect `GOOGLE_API_KEY` and rate limits. The CLI falls back to raw JSON when summarisation fails.
- **Emails missing:** Confirm `resend` is installed, the API key is set, and `RESEND_FROM_EMAIL` (or `--email-from`) is a verified sender.

Worst case scenario: set `--log-level DEBUG` and feed results to ChatGPT. It's probably DNS.

## License

RSS Morning ships under the Apache License 2.0. See `LICENSE` for the full text.

Happy briefing (and brewing)!
