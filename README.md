# RSS Morning

Some of us just want to sip the first coffee of the day and know whether the world is on fire. Others (hi) are on call for the industry gossip mill and still want to keep the caffeine sacred. RSS Morning is the homebrew rig I built so I can stay in the loop without doomscrolling twenty browser tabs. It slurps the headlines from your carefully curated feeds, bins the junk with embeddings, writes summaries that sound like a thoughtful teammate, and — if you want — emails the whole thing to you before your coffee is cold.

<img src="static/screenshot.png" style="max-width: 500px"/>

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

1. **Install dependencies:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Setup configuration:**
   The app is now fully validatable via XML configs.
   ```bash
   cp configs/config.xml.example configs/config.xml
   cp configs/env.xml.example configs/env.xml
   cp feeds.example.xml feeds.xml
   cp prompt-example.md prompt.md
   cp queries.example.txt queries.txt
   ```
3. **Customize:**
   - Edit `configs/config.xml` to point to your `feeds.xml` and `env.xml`.
   - Edit `configs/env.xml` with your API keys.
   - Edit `feeds.xml` with your RSS sources.
4. **Pre-brew embeddings (Optional):**
   ```bash
   python -m rss_morning.prefilter_cli \
      --output query_embeddings.json \
      --queries-file queries.txt
   ```
   Then update `<embeddings-path>` in `configs/config.xml` to point to this JSON file.
5. **Fire it up:**
   ```bash
   # Run with default config (configs/config.xml)
   python main.py

   # Or specify a different config
   python main.py --config configs/my-config.xml
   ```

## Tune The Inputs

### Configuration (`configs/config.xml`)
This is the control center. Use it to set:
- **Paths**: Locations of `feeds.xml`, `env.xml`, scripts, etc.
- **Runtime options**: `limit`, `max-age-hours`, `concurrency`.
- **Features**: Toggle `summary`, `pre-filter`, `database`.
- **Email**: `to`, `from`, and `subject`.

### Feeds (`feeds.xml`)
Standard OPML format. The `feeds.example.xml` shows how to structure it.

### Prompt (`prompt.md`)
Referenced in your `config.xml` (e.g., `<prompt file="../prompt.md" />`). This is the system prompt for Gemini.

### Queries (`queries.txt`)
Used by the pre-filter. A plain text file with one signal/topic per line.

## Secret Sauce (Environment Variables)

Secrets are loaded from the file specified in `configs/config.xml` (usually `configs/env.xml`), or from system environment variables.

- `OPENROUTER_API_KEY`: For OpenRouter summaries.
- `RESEND_API_KEY` & `RESEND_FROM_EMAIL`: For sending emails.

## Drive It From The CLI

Most settings live in `config.xml`, but the CLI offers useful overrides and utilities:

```bash
# Standard run
python main.py --config configs/production.xml

# Override logging
python main.py --log-level DEBUG --log-file mylog.txt

# Dry run LLM (logs request but doesn't call API)
python main.py --llm-dry-run

# Save/Load articles (great for debugging without spamming RSS feeds)
python main.py --save-articles debug_articles.json
python main.py --load-articles debug_articles.json

# Send a test email from a generic JSON payload
python main.py --send-email-from-json ./gemini-response.json
```

## Embeddings: The Pre-Filter Loop

1. **Configure queries** in `queries.txt`.
2. **Generate embeddings** (one-time setup for speed, or let it run on-the-fly):
   ```bash
   python -m rss_morning.prefilter_cli --output query_embeddings.json --queries-file queries.txt
   ```
3. **Enable in config**:
   In `config.xml`, set `<pre-filter><enabled>true</enabled>` and point `<embeddings-path>` to your JSON file.

   If you skip the JSON path, the app will compute embeddings at runtime (slower startup).

   You can also configure the clustering aggressiveness via `<cluster-threshold>`.

## Docker

Build and run:
```bash
docker compose up --build
```
Map your `configs/` folder and `feeds.xml` into the container to persist settings.

## Email Delivery

Enable it in `configs/config.xml` by setting the `<email>` block.
You can also verify your email rendering/delivery (without running the full fetch loop) using:
```bash
python verify_email.py
```

## Testing

```bash
pytest
```

## Troubleshooting

- **Check logs:** `logs/rss-morning.log` is your friend.
- **Debug mode:** Run with `--llm-dry-run` to see what would be sent to Gemini.
- **Email issues:** Use `verify_email.py` to isolate rendering vs. sending problems.

## License

RSS Morning ships under the Apache License 2.0. See `LICENSE` for the full text.

Happy briefing (and brewing)!
