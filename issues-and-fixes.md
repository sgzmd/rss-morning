# RSS Morning stability, cost, and performance remediation plan

This document is an execution plan for a coding agent with a small context window.
Follow it literally and sequentially. Do not skip steps, combine branches, or improvise
around a failed guardrail. The objective is to improve RSS Morning without changing its
documented JSON contracts or its deliberate partial-failure behavior.

The plan uses full test-driven development (TDD), requires 100% line and branch coverage,
and isolates every change on its own Git branch. It also maintains a small ignored state
file so that an agent can safely continue after context compaction or a new session.

## 1. Required end state

When all work is complete, all of the following must be true:

1. A cached article with no extracted text is never silently dropped.
2. Token truncation cannot make the pipeline fail merely because runtime network access is
   unavailable.
3. Successfully extracted full article text is cached before output-specific truncation.
4. Article and embedding cache entries cannot be incorrectly reused after content,
   provider, model, or preprocessing changes.
5. Database reads and writes are batched and are not committed once per article worker.
6. Feed and article HTTP traffic uses connection reuse, bounded response sizes, explicit
   connect/read timeouts, safe retries, and per-host concurrency limits.
7. Feed downloads use conditional requests when a persistent database cache is enabled.
8. Precomputed query embeddings use a versioned runtime-compatible format.
9. `cluster-threshold` has real, tested semantic-deduplication behavior.
10. A documented two-stage metadata-first prefilter can avoid fetching pages that will
    certainly not be summarized, while the existing full-text mode remains available.
11. Summarization defaults to a configurable, price-capped OpenRouter model chain rather
    than the direct Gemini API. LLM requests use token-aware batches, bounded retry
    behavior, strict response reconciliation, and exact-input caching without changing the
    final JSON shape.
12. Configuration is validated before worker pools or external calls start.
13. Feed timestamps are interpreted as UTC on every host timezone.
14. The production container contains runtime dependencies only, prewarms tokenizer data,
    and still runs as a non-root user.
15. Logs expose stage duration, counts, cache hits, and provider usage without exposing
    prompts, article bodies, credentials, connection strings, or full provider responses
    at INFO level.
16. `README.md`, `AGENTS.md`, `configs/config.xml.example`, code, and tests describe the
    same behavior.
17. Hermetic tests have 100% statement and branch coverage over `rss_morning` and
    `main.py`.
18. Ruff, formatting, mypy, Python 3.11 tests, and Python 3.12 tests all pass.
19. The live end-to-end test passes once the operator explicitly approves its network and
    API cost.

## 2. Non-negotiable behavior invariants

These are hard constraints. If a proposed implementation conflicts with one, keep the
invariant and redesign the implementation.

| Area | Invariant |
| --- | --- |
| Raw output | With summaries disabled, stdout remains a JSON list of article dictionaries. |
| Summary output | With summaries enabled, stdout remains an object containing `summaries` and optionally `exec_summary`. |
| Article fields | Preserve `url`, `category`, `title`, `summary`, `published`, optional `text`, and optional `image`. |
| Prefilter fields | Preserve `prefilter_score`, `prefilter_match`, and `other_urls` when applicable. |
| Ordering | Preserve category-ascending ordering and published-descending ordering within a category unless an existing test proves a different contract. |
| Feed failure | One failed feed is logged and skipped; it does not abort other feeds. |
| Article failure | One failed page is logged and represented by feed metadata when possible; it does not abort other pages. |
| Prefilter failure | A prefilter failure fails open and returns the original articles. It must not return a partially mutated list. |
| LLM failure | A failed LLM batch is logged and omitted after bounded recovery attempts; successful batches remain. |
| Email failure | Email failure is logged and does not change a successful process exit into failure. |
| Top-level failure | Invalid configuration and unrecoverable orchestration errors return exit code 1. |
| Dry run | `--llm-dry-run` makes no model or email call and does not expose input at INFO. |
| Replay | `--load-articles` performs no feed or article HTTP calls. |
| Secrets | Never log API keys, database connection strings, prompt text, article bodies, or provider response bodies at INFO or above. |
| User data | Never modify ignored configs, feeds, prompts, databases, logs, snapshots, model caches, or credentials unless a test creates them under `tmp_path`. |
| Network in unit tests | Unit tests must never use sockets. Every external boundary must use a fake. |

Do not “fix” an issue by deleting a feature, reducing error handling, changing output
shapes, or weakening a test.

## 3. First-session bootstrap and safety protocol

Perform these steps before touching code.

### 3.1 Read instructions and record the baseline

Run:

```bash
pwd
sed -n '1,260p' AGENTS.md
sed -n '1,260p' issues-and-fixes.md
git status --short
git branch --show-current
git log -5 --oneline --decorate
python main.py --help
```

Do not display or read populated ignored XML files, feeds, prompts, databases, snapshots,
or environment files. The filenames may be recorded, but their contents are user data.

If `git status --short` already contains changes, copy the exact status into the state
file described below. Treat those paths as owned by the user. Never stage, restore,
overwrite, reformat, or delete them unless this plan explicitly owns the same path. If an
existing change overlaps a file required by a branch, stop and ask the operator rather
than overwriting it.

Forbidden commands throughout this plan:

```text
git reset --hard
git checkout -- <path>
git clean
git stash --include-untracked
rm -rf
git push --force
git commit -a
git add .
git add -A
```

Stage only explicit paths with `git add path1 path2 ...`.

### 3.2 Create ignored durable agent state

The repository's `.gitignore` already ignores dot-prefixed paths. Create this local-only
directory and file:

```text
.agent-state/issues-and-fixes-progress.md
```

Use this exact template:

```markdown
# RSS Morning remediation progress

## Baseline
- Original branch: <branch>
- Original commit: <full SHA>
- Pre-existing status: <exact `git status --short`, or `clean`>
- Python versions available: <versions>
- Baseline hermetic tests: <command and result>
- Baseline coverage: <percentage and missing lines>

## Current position
- Integration branch: codex/stability-cost-speed
- Current issue: <number and name>
- Current leaf branch: <branch>
- TDD phase: RED | GREEN | REFACTOR | DOCS | VERIFY | MERGED
- Last known good commit: <SHA>
- Next exact action: <one command or one edit>

## Completed issues
- <issue>: <merge SHA>; tests: <commands>; coverage: 100%

## Decisions
- <date>: <decision, evidence, and why>

## Blockers
- <none, or exact blocker and evidence>

## Files owned by the user
- <pre-existing changed/untracked paths; never touch>
```

Update this file:

- before and after every commit;
- before changing branches;
- before running a command likely to take more than one minute;
- whenever a test fails for a reason not yet understood;
- immediately before ending a session or when context is getting small.

Never commit `.agent-state/`.

### 3.3 Small-context restart procedure

At the beginning of every new or compacted context, read only:

1. `AGENTS.md`;
2. sections 1–6 of this plan;
3. `.agent-state/issues-and-fixes-progress.md`;
4. the subsection for the current issue;
5. `git status --short` and `git log -5 --oneline --decorate`;
6. only the production and test files named by the current issue.

Do not reread the entire repository. Do not rely on memory. The state file and tests are
the source of truth.

If the current branch or commit differs from the state file, stop. Determine which is
newer using `git log`, update the state file, and rerun the current issue's focused tests
before editing.

## 4. Git branch choreography

This plan uses one cumulative integration branch and one short-lived leaf branch for each
issue.

### 4.1 Create the integration branch once

After the plan itself is committed on the operator's starting branch:

```bash
git switch -c codex/stability-cost-speed
```

Do not implement changes directly on this branch. It is only the cumulative merge target.

### 4.2 Start every issue branch the same way

From the integration branch:

```bash
git switch codex/stability-cost-speed
git status --short
git switch -c codex/<issue-branch-name>
```

The status must contain only the pre-recorded user-owned paths and ignored agent state. If
there are unexplained modifications, stop.

### 4.3 Every issue has at least two commits

Commit 1 is the RED test commit:

```text
test(<area>): reproduce <specific failure>
```

Before this commit, run the new focused test and record its expected failure in the state
file. The failure must demonstrate the missing behavior, not a syntax error, import error,
or broken fake.

Commit 2 is the GREEN implementation commit:

```text
fix(<area>): <specific behavior now guaranteed>
```

If refactoring or documentation is substantial, use additional commits:

```text
refactor(<area>): <internal change with no behavior change>
docs(<area>): document <new behavior>
```

Never commit a branch whose tip is red.

### 4.4 Verify and merge every issue

At the tip of every leaf branch, run the full gate in section 6. Then inspect:

```bash
git diff --check codex/stability-cost-speed...HEAD
git diff --stat codex/stability-cost-speed...HEAD
git diff --name-status codex/stability-cost-speed...HEAD
```

Confirm every changed file belongs to the issue. Then:

```bash
git switch codex/stability-cost-speed
git merge --no-ff codex/<issue-branch-name>
```

Rerun the full gate on the integration branch. Only after it passes may the next issue
start. Keep the leaf branch until final acceptance so it remains an easy diagnostic and
rollback point.

If merging causes a test or coverage failure, resolve it on a new branch named
`codex/<issue>-integration-fix`; do not patch the integration branch directly.

## 5. Mandatory TDD method

For every behavior change, follow this sequence exactly.

### RED

1. Write the smallest test that expresses one externally visible behavior.
2. Use deterministic fakes. Do not sleep, use real sockets, or depend on wall-clock
   performance.
3. Run only the new test with `-vv`.
4. Confirm it fails for the intended reason.
5. Record the failure in state.
6. Commit the failing test.

### GREEN

1. Implement the smallest correct behavior.
2. Run the new test.
3. Run the containing test module.
4. Run all tests directly related to the modified boundary.
5. Do not refactor unrelated code while making the test pass.

### REFACTOR

1. Remove duplication and improve names without changing behavior.
2. Run focused tests after each meaningful edit.
3. Add tests for every new error branch and fallback.
4. Run mutation checks mentally: temporarily invert important conditions or return the
   wrong cached value and verify a test would fail. Do not commit mutations.

### VERIFY

1. Run the full hermetic suite with coverage.
2. Run Ruff, formatting, and mypy.
3. Confirm no test opened a socket.
4. Inspect the diff for accidental public-contract changes.
5. Update README, AGENTS, example configuration, and docstrings in the same issue branch
   whenever behavior changed.

Tests must assert output and side effects, not implementation trivia. Prefer call-count
assertions at expensive boundaries: HTTP requests, extractor calls, embedding calls,
database commits, and LLM calls.

## 6. Coverage and quality gate

Coverage means 100% statements and 100% branches for all first-party runtime code. Merely
reaching lines is not sufficient; tests must assert behavior.

Use this full gate at the end of every issue:

```bash
ruff check .
ruff format --check .
mypy rss_morning main.py
pytest -m "not live_e2e" \
  --cov=rss_morning \
  --cov=main \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=100
git diff --check
```

Rules:

- Do not lower `fail_under`.
- Do not add `# pragma: no cover` to avoid writing a test.
- Existing legitimate optional-dependency and defensive pragmas may remain.
- A new pragma requires an explanation in the state file and must describe a truly
  unreachable platform or interpreter branch. Prefer dependency injection instead.
- Do not use broad exception swallowing in tests.
- Do not mock the function under test.
- Do not delete assertions or weaken exact expected values to regain green.
- If coverage drops below 100%, the current issue is not complete.

## 7. Sequential issue branches

Complete the following branches in order. Later branches may rely on earlier ones.

---

## Issue 00 — Establish the coverage and test guardrail

Branch: `codex/00-coverage-guardrail`

### Purpose

Make 100% line and branch coverage an executable, permanent repository rule before
changing production behavior.

### RED tests and configuration

1. Create `requirements-dev.txt` containing `-r requirements.txt` plus pinned development
   tools: pytest, pytest-cov, Ruff, mypy, pre-commit, and request type stubs.
2. Create `.coveragerc` with `branch = True`, sources `rss_morning` and `main`, and
   `fail_under = 100`.
3. Add a `coverage` Make target and make `test` run the hermetic suite without the live
   marker. Add a separate `check` target for the full gate.
4. Update CI to install `requirements-dev.txt` and run `make check` under Python 3.11.
5. Run the coverage command. It should fail below 100%. Record the exact missing lines and
   branches. This is the RED evidence.

### GREEN work

Close existing coverage gaps module by module in this order:

1. `models.py` and `config.py`;
2. `feeds.py` and `articles.py`;
3. `db.py` and `embeddings.py`;
4. `prefilter.py` and `prefilter_cli.py`;
5. `summaries.py`;
6. renderers, templating, and emailing;
7. `runner.py`, `cli.py`, and `main.py`.

For each uncovered branch, add a behavior test. Examples include malformed XML, absent
optional fields, empty inputs, invalid dates, missing optional dependencies, database
rollback, malformed provider output, email fallback text, and CLI exit-code paths.

Do not alter production behavior merely to make a line easier to cover. Small dependency-
injection refactors are allowed only after characterization tests capture current behavior.

### Definition of done

- The full section 6 gate passes at 100%.
- CI uses the same command developers use.
- Live tests remain opt-in and excluded from the coverage denominator.
- README and AGENTS document `make check` and the 100% branch requirement.

---

## Issue 01 — Fix article-cache correctness and cache raw extraction

Branch: `codex/01-article-cache-correctness`

### Confirmed failures to address

- A cache row whose `content` is `NULL` is treated as a hit and passed to
  `truncate_text`, causing the article worker to catch an exception and return `None`.
- Newly fetched text is currently truncated before being written to the database, so a
  later increase in `max-article-length` cannot recover the original extraction.
- Transient extraction failures can be cached as if extraction succeeded.

### RED tests

Add tests in `tests/test_runner.py` and `tests/test_db.py` proving:

1. Seed a cached article with `text=None`; execute a run; the page extractor is called
   again and the metadata article is not dropped if extraction still fails.
2. A first successful extraction with output limit 10 stores the complete raw text in the
   database while stdout contains only 10 tokens.
3. A second run with output limit 100 uses the cached full text and returns more than the
   original 10-token output without calling the extractor.
4. An extraction with no text is not written as a successful content cache entry.
5. Feed category always overrides cached category, preserving the existing contract.
6. Cached image and publication values are restored as before.
7. A cache read failure affects only that article and follows the existing recoverable
   behavior.

The first three tests must fail against current code for the expected reasons.

### GREEN design

1. Build a raw cache payload from feed metadata plus untruncated extracted text.
2. Write only successful text extractions to the article-content cache. It is acceptable
   to return metadata immediately; the next run must retry extraction.
3. Build a separate output payload by copying the raw payload and truncating the copy.
4. On cache read, consider `content is None` a miss, not a hit.
5. Make `truncate_text` accept only strings; guard optional text at its caller so type
   errors cannot be silently hidden.
6. Preserve cached rows created by older versions. Do not require destructive migration.

### Guardrails

- Do not add a global cache clear.
- Do not rewrite the user's existing database.
- Do not change stdout fields or ordering.
- Do not cache a failed extraction forever.

### Definition of done

Focused cache tests, `tests/test_runner.py`, `tests/test_db.py`, and the full section 6 gate
all pass.

---

## Issue 02 — Make token truncation offline-safe and single-initialized

Branch: `codex/02-tokenizer-offline-safety`

### Desired behavior

The first article processed in a fresh environment must not be lost because tiktoken's
encoding data cannot be downloaded. Concurrent workers must not race to initialize or
download the same encoder.

### RED tests

Add tests proving:

1. Encoder initialization is called once for many `truncate_text` calls.
2. Concurrent truncation initializes the encoder once.
3. If encoder initialization raises a network-related or generic runtime exception,
   truncation returns a deterministic conservative character-limited string and logs one
   warning without article content.
4. `None` is rejected at the `truncate_text` boundary and is safely handled by callers.
5. Zero or negative limits fail configuration validation before any article work.
6. A short string returns unchanged.
7. Unicode fallback truncation is deterministic and does not produce invalid text.
8. A container/offline smoke test can truncate text with network disabled.

### GREEN design

1. Add a small `TokenTruncator` abstraction or a cached `get_encoder()` helper protected
   by a lock. Initialize it before submitting article futures when exact token truncation
   is required.
2. Keep exact cl100k behavior when encoder data is available.
3. Add a conservative fallback, documented as an approximation. Use a simple deterministic
   character budget; do not import another network-backed tokenizer.
4. Log only the exception class and operational message. Never log input text.
5. In Docker, set an explicit tokenizer cache directory, prewarm `cl100k_base` during the
   image build, and ensure the non-root runtime user can read it.
6. Do not make module import itself fail if prewarming is absent.

### Definition of done

- Exact and fallback paths both have line and branch coverage.
- The unit suite proves no socket is used.
- Docker offline smoke behavior is specified in a test or build verification script.
- The full gate passes.

---

## Issue 03 — Batch article database access

Branch: `codex/03-batch-article-cache`

### Desired behavior

Selected URLs should be read in one database operation. Successful new extractions should
be written in one transaction after workers finish. Database sessions must not be opened
and committed once per article worker.

### RED tests

Add tests proving:

1. `get_articles(session, urls)` returns a URL-keyed mapping and handles empty input.
2. `upsert_articles(session, payloads)` inserts and updates multiple rows with one commit.
3. A write failure rolls back the entire batch.
4. One execution with N selected articles performs one bulk cache read and at most one bulk
   cache write.
5. Worker threads never receive a SQLAlchemy session.
6. A mixed set of hits, misses, and extraction failures preserves output ordering and
   partial-failure behavior.
7. Duplicate URLs are removed before cache lookup.

Use fake repository/session objects with explicit call counts; do not assert private local
variable names.

### GREEN design

1. Add bulk database functions while retaining single-row wrappers only where existing
   tests or callers require them.
2. Load cached articles after global URL deduplication and before the article worker pool.
3. Give workers immutable cached payload data, never sessions.
4. Collect raw successful extractions and bulk-upsert them after the pool completes.
5. If the bulk write fails, log the cache failure but preserve already-fetched article
   output. Caching is an optimization and must not destroy the digest.
6. Use `expire_on_commit=False` if necessary, but do not keep ORM objects outside sessions.

### Definition of done

One deterministic orchestration test demonstrates O(1) database transactions with respect
to article count. The full gate passes.

---

## Issue 04 — Introduce a resilient shared HTTP boundary

Branch: `codex/04-resilient-http-client`

### Desired behavior

Feed and page downloads must share one well-tested first-party HTTP abstraction. It must
reuse connections inside each worker thread, bound resources, retry only safe transient
failures, and enforce per-host concurrency.

### RED tests

Create `tests/test_http_client.py`. Test with fake sessions/responses:

1. Separate connect and read timeouts are passed.
2. GET retries are configured only for connect failures, timeouts, 429, 500, 502, 503,
   and 504.
3. `Retry-After` is respected by the retry policy.
4. Permanent 4xx responses are not retried.
5. Redirects return the final URL.
6. A response exceeding the byte limit is aborted and raises a typed recoverable error.
7. A missing or unacceptable content type is handled according to the caller's policy.
8. Sessions are reused within one worker and are not shared unsafely across threads.
9. No more than the configured number of requests to one hostname are active at once.
10. Different hostnames may progress independently.
11. Response bodies and authorization headers never appear in logs.

Add feed and article boundary tests proving they use the new client without real sockets.

### GREEN design

1. Add `rss_morning/http_client.py` with typed request and response records and typed
   exceptions such as `DownloadError`, `ResponseTooLarge`, and `UnsupportedContentType`.
2. Use thread-local `requests.Session` instances with mounted adapters. The client object
   may be shared; mutable sessions may not.
3. Stream response bodies and enforce configurable maximum bytes.
4. Set an explicit User-Agent identifying RSS Morning.
5. Maintain a lock-protected hostname-to-semaphore mapping. The default per-host limit
   should be small, such as 2; make it configurable.
6. Keep retries bounded with exponential backoff and jitter. Never retry non-idempotent
   operations.
7. Return final URL, status, normalized headers, and bytes.
8. Adapt feed fetching to consume downloaded bytes.
9. Adapt article extraction to consume downloaded HTML rather than allowing extractor
   libraries to perform uncontrolled downloads. Newspaper can parse supplied HTML; ensure
   lead-image URL resolution uses the final redirected URL. Trafilatura should extract from
   the same supplied document.
10. Keep `fetch_feed_entries` and `fetch_article_content` easy to fake. Preserve compatible
    defaults for existing callers.

### Configuration additions

Add validated XML settings with conservative defaults:

```xml
<http>
  <connect-timeout>5</connect-timeout>
  <read-timeout>20</read-timeout>
  <max-feed-bytes>5242880</max-feed-bytes>
  <max-article-bytes>10485760</max-article-bytes>
  <retries>2</retries>
  <backoff-seconds>0.5</backoff-seconds>
  <per-host-concurrency>2</per-host-concurrency>
</http>
```

Update config parser tests, example config, README, and AGENTS.

### Guardrails

- Unit tests may not use actual HTTP servers or sockets.
- Do not retry certificate errors, invalid URLs, or permanent 4xx responses.
- Do not remove the current per-feed and per-article recovery behavior.
- Do not log response bodies.

### Definition of done

All extractor success/failure/image tests pass through supplied HTML. Deterministic tests
prove retry and concurrency limits. The full gate passes.

---

## Issue 05 — Add persistent conditional feed fetching

Branch: `codex/05-conditional-feed-cache`

### Desired behavior

When the database is enabled, repeated runs should send `If-None-Match` and/or
`If-Modified-Since`. A 304 response should reuse the previously stored feed body. With the
database disabled, behavior should remain a normal unconditional fetch.

### RED tests

Add tests proving:

1. A 200 feed response stores body, final URL, ETag, Last-Modified, and fetch timestamp.
2. The next request sends the correct conditional headers.
3. A 304 parses the cached body and produces the same entries without redownloading it.
4. A 304 with missing or corrupt cached body makes one unconditional recovery request.
5. A changed 200 response atomically replaces prior state.
6. Feed state is bulk-read and bulk-written, not committed per worker.
7. Database-disabled runs do not read or write feed cache state.
8. One corrupted cache row affects only its feed.
9. Redirected final URLs do not lose the original configured-feed identity.

### GREEN design

1. Add a new table rather than altering an existing user table in place. For example,
   `feed_http_cache` keyed by configured feed URL.
2. Store bounded raw feed bytes plus validators. The HTTP size limit prevents unbounded DB
   growth.
3. Bulk-load states before the feed pool. Give each worker immutable state. Bulk-write
   successful updates after workers finish.
4. Preserve the last good body when a transient network error occurs.
5. Do not treat old cached content as a successful current fetch after an unconditional
   error unless the behavior is explicitly configured and documented. Default to current
   partial-failure behavior.

### Definition of done

Call-count tests prove that a 304 transfers no body and still returns entries. The full
gate passes.

---

## Issue 06 — Make embedding caches content- and provider-correct

Branch: `codex/06-embedding-cache-correctness`

### Confirmed failures to address

The current persistent cache is keyed by URL and model only. The in-memory centroid cache
also omits provider and preprocessing identity. Vector counts and dimensions are not
strictly checked.

### RED tests

Add tests proving:

1. Same URL and unchanged content/provider/model/preprocessing gives a cache hit.
2. Same URL with changed title, summary, or text is a miss.
3. Same model label with a different provider is a miss.
4. A preprocessing-version change is a miss.
5. Query centroids from different providers never collide in memory.
6. A backend returning fewer or more vectors than inputs raises a typed validation error;
   the prefilter fails open with the original unmutated articles.
7. A vector-dimension mismatch is rejected rather than truncated with `zip`.
8. A corrupt persistent vector is ignored and replaced.
9. Zero vectors are handled deterministically.
10. Cached vectors are restored in exact input order.
11. Failure after scoring begins returns pristine original categories and fields.

### GREEN design

1. Define a canonical preprocessing version constant.
2. Hash the exact normalized embedding input with SHA-256.
3. Use a backend identity containing provider, model, and preprocessing version.
4. Avoid an in-place migration of the existing `embeddings` table. Create a versioned
   `embeddings_v2` table with explicit URL, input hash, backend identity, dimension, vector
   bytes, and timestamp columns. Do not trust legacy model-only entries.
5. Store numeric vectors compactly, for example as float32 bytes, and validate byte length
   before decoding.
6. Require `len(vectors) == len(inputs)` and identical nonzero dimensions.
7. Use `numpy.dot` only after dimension validation.
8. Never mutate caller-owned article dictionaries until all embedding and scoring work has
   succeeded. Work on copies and publish the result at the end.
9. Bound the in-memory centroid cache or use a small LRU so a long-running process cannot
   grow forever.

### Definition of done

Tests explicitly demonstrate all previous collision and silent-truncation cases. The full
gate passes.

---

## Issue 07 — Restore precomputed query embeddings and real clustering

Branch: `codex/07-prefilter-query-cache-clustering`

### Desired behavior

The legacy export command and runtime must share one validated versioned format.
`cluster-threshold` must remove semantically duplicate stories from the returned candidate
set and attach duplicates as `other_urls` to their representative.

### RED tests: query file

1. Export flattens every actual `(category, query)` pair; it must not embed category names
   as queries.
2. Exported JSON contains format version, provider, model, preprocessing version,
   dimensions, categories, exact queries, and vectors.
3. Runtime accepts a complete matching file and performs zero query-embedding calls.
4. Runtime rejects a stale provider/model/version, missing query, duplicate query, corrupt
   vector, or wrong dimension and recomputes safely.
5. File writes are atomic: write a temporary sibling and replace only after valid JSON is
   complete.
6. Runtime failure to read the optional file logs a concise warning and computes queries;
   it does not abort article processing.

### RED tests: clustering

1. Items are processed by descending relevance score.
2. If candidate similarity is greater than or equal to `cluster-threshold`, it is not
   returned as a separate summary candidate.
3. The duplicate URL and rounded distance appear once in the nearest retained
   representative's `other_urls`.
4. A sufficiently different item remains a separate representative.
5. The maximum cluster size applies to retained representatives, not duplicates.
6. Clustering is deterministic under input reordering when scores differ.
7. Exact score ties use a documented stable tiebreaker, such as publication time then URL.
8. Threshold boundary values 0 and 1 are validated and tested.
9. A category with no passing items returns nothing, as before.

### GREEN design

1. Define and document format version 2 in one module used by CLI and runtime.
2. Export actual query strings while retaining their category association.
3. Load only exact-compatible data. Never silently mix vector dimensions or models.
4. Implement greedy representative selection after relevance sorting:
   - compare a candidate to retained representatives in its category;
   - when similarity meets the threshold, attach it to the nearest representative;
   - otherwise retain it as a new representative;
   - stop after `max_cluster_size` representatives, but continue only as needed to attach
     known duplicates according to the documented limit.
5. Define `cluster-threshold` explicitly as cosine similarity in `[0, 1]`.
6. Remove obsolete comments claiming the settings are ignored.

### Contract decision

The returned article list becomes the set of representatives. Duplicate source URLs remain
available through `other_urls`. This is the intended cost-saving behavior and must be
documented as an explicit prefilter behavior change. Raw non-prefilter output is unchanged.

### Definition of done

The legacy CLI tests and runtime tests use the same fixture file. A call-count assertion
proves compatible precomputed queries incur no embedding API/model work. The full gate
passes.

---

## Issue 08 — Add an opt-in two-stage metadata-first prefilter

Branch: `codex/08-two-stage-prefilter`

### Safety decision

Do not silently change the default selection algorithm. Add a documented mode:

```xml
<pre-filter>
  <mode>full-text</mode>
  <candidate-multiplier>3</candidate-multiplier>
</pre-filter>
```

Supported modes are `full-text` and `metadata-first`. Keep `full-text` as the compatibility
default. The example may recommend `metadata-first`, but flipping the runtime default
requires separate operator approval after live A/B evidence.

### Metadata-first algorithm

1. Fetch and deduplicate feed entries.
2. Bulk-load article cache.
3. For cached successful articles, full text is already cheap and may be used.
4. For uncached entries, embed only title and feed summary.
5. Keep up to `candidate-multiplier * max_cluster_size` candidates per query category.
6. Fetch pages only for retained candidates.
7. Perform the normal full-text scoring and clustering over those candidates.
8. Return the same output fields and ordering as full-text mode.
9. If metadata embedding fails, fail open to the existing full-text path, not to an empty
   result.

### RED tests

1. In `full-text` mode, extractor call counts and selection match characterization tests
   from before this branch.
2. In `metadata-first` mode, rejected entries cause zero page downloads.
3. Cached full text can contribute without a page download.
4. Candidate multiplier is applied per category and validated as a positive integer.
5. First-stage failure invokes the full-text compatibility path.
6. Second-stage failure preserves the existing prefilter fail-open behavior.
7. Deduplication occurs before either embedding or page download.
8. A deterministic synthetic workload proves page-download calls are strictly fewer than
   in full-text mode while final representatives are identical for the fixture.
9. Snapshot replay mode performs no HTTP in either prefilter mode.

### Guardrails

- Do not use timing assertions; use external-call counts.
- Do not inspect the user's private prompt, feeds, configs, or snapshots.
- Do not lower relevance thresholds to make a fixture pass.
- Keep the compatibility default until explicitly approved.

### Definition of done

The deterministic fixture demonstrates the cost reduction and identical output. Config,
README, AGENTS, and example XML are aligned. The full gate passes.

---

## Issue 09 — Move summarization to low-cost configurable OpenRouter routing

Branch: `codex/09-llm-resilience-cost`

### Researched model recommendation and price snapshot

Prices below were checked on 2026-08-09. They are USD list prices per one million tokens
and may change. They do not include OpenRouter's current 5.5% fee when purchasing credits;
budget and cost reports must show inference list cost and credit-purchase overhead
separately rather than blending them. The implementing agent must re-query OpenRouter's
public Models API at the start of this branch, save a sanitized price/capability snapshot
as a committed test fixture, and record the retrieval date. Do not make a live model call
merely to retrieve catalog metadata.

| Role | Model | Input | Output | Context | Reason for placement |
| --- | --- | ---: | ---: | ---: | --- |
| Recommended default | `bytedance-seed/seed-2.0-mini` | $0.10 | $0.40 | 262K | Designed for cost- and latency-sensitive work; OpenRouter reported a low structured-output error rate. This is a stronger fit for compact news summarization than selecting a coding-specialized model solely on price. |
| Economy fallback | `z-ai/glm-4.7-flash` | $0.06 | $0.40 | 203K | Lowest input price of the shortlisted non-Gemini models, several provider endpoints, and `response_format` support. Keep it behind the default until the repository's summary-quality evaluation passes. |
| Reliability fallback | `openai/gpt-4o-mini` | $0.15 | $0.60 | 128K | Slightly more expensive, but mature strict structured-output support and low reported structured-output error rates. |
| Comparison only | `google/gemini-2.5-flash` through OpenRouter | $0.30 | $2.50 | 1M | Useful cost baseline, not part of the default non-Gemini chain. |

Recommended initial chain:

```text
bytedance-seed/seed-2.0-mini
  -> z-ai/glm-4.7-flash
  -> openai/gpt-4o-mini
```

Relative to the OpenRouter Gemini 2.5 Flash comparison price, the recommended default's
list price is about 67% lower for input and 84% lower for output. This comparison is only
illustrative: actual cost must be calculated from response usage and the model actually
selected by OpenRouter.

Primary sources:

- [OpenRouter Seed-2.0-Mini pricing and performance](https://openrouter.ai/bytedance-seed/seed-2.0-mini/pricing)
- [OpenRouter GLM 4.7 Flash pricing and parameters](https://openrouter.ai/z-ai/glm-4.7-flash)
- [OpenRouter GPT-4o-mini parameters and structured-output metrics](https://openrouter.ai/openai/gpt-4o-mini?tab=parameters)
- [OpenRouter Gemini 2.5 Flash comparison pricing](https://openrouter.ai/google/gemini-2.5-flash)
- [OpenRouter structured-output requirements](https://openrouter.ai/docs/guides/features/structured-outputs)
- [OpenRouter model fallback behavior](https://openrouter.ai/docs/guides/routing/model-fallbacks)
- [OpenRouter provider routing and price caps](https://openrouter.ai/docs/guides/routing/provider-selection)
- [OpenRouter billing and credit-purchase fees](https://openrouter.ai/docs/faq)

Do not select a `:free` model as the production default. Free variants have materially
different rate limits and availability, which conflicts with a scheduled morning digest.

### Desired behavior

Avoid fixed 100-article failure domains. Recover from transient provider failures without
unbounded cost. Reject hallucinated or duplicate result identities. Cache only exact valid
batch inputs so semantics are never incorrectly reused. Use OpenRouter by default with a
configurable ordered model chain, strict structured-output capability routing, and a hard
maximum price. Retain the direct Gemini adapter as an explicitly configured compatibility
option for existing users, but do not use it by default.

### RED tests: provider and model migration

1. With no `<llm>` element, parsed configuration selects provider `openrouter`, primary
   model `bytedance-seed/seed-2.0-mini`, and the two ordered fallbacks above.
2. A user can configure one primary OpenRouter model and zero to three fallback model IDs.
3. Model IDs must be nonempty OpenRouter slugs. Reject duplicate primary/fallback entries.
4. Direct Gemini remains selectable only with `<provider>gemini</provider>` and uses
   `GOOGLE_API_KEY`/`GEMINI_API_KEY` exactly as before.
5. Default OpenRouter operation requires `OPENROUTER_API_KEY`; it never reads a Google key.
6. The OpenRouter request uses strict JSON Schema and
   `provider.require_parameters=true`, so an endpoint lacking structured output cannot be
   selected.
7. The request sends the fallback models in exact configured order through OpenRouter's
   `models` routing field.
8. The request sends `provider.max_price` using configured per-million-token prompt and
   completion caps. A catalog or endpoint above either cap is unavailable rather than
   silently charging more.
9. The default cap admits all three recommended models but rejects the more expensive
   Gemini comparison model. Use a default such as $0.20/M input and $0.75/M output.
10. Configuration can choose provider sorting `price`, `throughput`, or `latency`; default
    to `price` while retaining provider fallbacks.
11. The actual response `model` and token usage are captured in internal metrics, proving
    which fallback was billed, but never alter stdout JSON.
12. LLM cache identity includes the actual configured model chain, routing policy, and
    price caps. Changing any of them is a cache miss.
13. A malformed catalog response, catalog network failure, or price change cannot break a
    normal run because runtime catalog lookup is not required for every digest.
14. The committed catalog fixture is used for validation tests only; production sends
    OpenRouter's native `max_price` cap on every request.

### RED tests: model-quality gate

Create a small, committed, synthetic evaluation corpus containing at least:

- straightforward news;
- several near-duplicate reports;
- an article with weak feed metadata but useful body text;
- Unicode titles and bodies;
- adversarial HTML/instructions inside article text;
- long input near the batch budget;
- multiple categories;
- expected source URLs and required summary-field constraints.

The hermetic suite uses recorded provider-shaped responses and must assert schema,
identity, sanitization, and completeness. Separately provide an opt-in evaluation command
that can call each candidate model on the same corpus and report:

- valid structured-response rate;
- source URL precision and recall;
- required-field completeness;
- duplicate/hallucinated URL count;
- input/output tokens;
- calculated list-price estimate;
- latency;
- normalized content-quality scores from deterministic checks.

Do not let an LLM judge be the only quality gate. The operator must explicitly approve any
paid candidate-model evaluation. Do not inspect or upload private prompts, feeds, articles,
or snapshots. If `z-ai/glm-4.7-flash` meets every deterministic requirement and its content
quality is accepted by the operator, it may become the configured economy choice; do not
silently promote it to default during this branch.

### RED tests: batching

1. Batches respect both a maximum article count and estimated input-token budget.
2. A single oversized article is truncated according to the existing configured content
   limit and placed in a bounded batch.
3. Batch construction is deterministic.
4. System-prompt tokens are counted in every batch.
5. Empty input performs no provider call.

### RED tests: retry and recovery

1. Timeout, connection failure, 429, and 5xx receive bounded exponential-backoff retries.
2. Authentication, permission, invalid-model, and other permanent 4xx errors are not
   retried.
3. `Retry-After` is honored when available.
4. Tests inject a fake sleeper; unit tests never actually sleep.
5. After retries, a failed multi-article batch is split in half and each half is tried.
6. A failed single-article batch is logged and omitted, preserving successful batches.
7. Maximum attempts and split depth prevent runaway calls. Assert exact worst-case call
   counts.
8. Explicit request timeouts reach both Gemini and OpenRouter clients.

### RED tests: response reconciliation

1. Every returned URL must exactly match one submitted URL.
2. Duplicate returned URLs are rejected or deterministically deduplicated according to one
   documented rule; prefer rejecting the malformed batch so it can be retried/split.
3. Missing required summary strings invalidate that item/batch according to schema policy.
4. Category is restored from source input rather than trusted from model output.
5. Missing articles are logged by count and URL-safe identifier; no article content is
   logged.
6. HTML is removed from all summary fields, including `rank-reasoning`.
7. Valid successful-batch order is restored to source order before the runner's documented
   category sort.
8. A hallucinated URL cannot acquire an image through `_attach_summary_images`.

### RED tests: exact-input cache

1. Cache identity contains provider, model, response-schema version, prompt hash, and a
   hash of the exact ordered batch input.
2. An identical validated batch is returned from cache with zero provider calls.
3. Changing any article field sent to the model, prompt, model, provider, order, or schema
   version is a miss.
4. Malformed, partial, or failed responses are never cached.
5. Dry-run mode neither reads cached output in place of showing preparation nor writes
   cache entries.
6. Cache read/write failure does not lose model output.
7. Cached payload is validated again before use; corrupt cache is ignored.

### GREEN design

1. Add explicit LLM settings with safe defaults:

   ```xml
   <llm>
     <provider>openrouter</provider>
     <model>bytedance-seed/seed-2.0-mini</model>
     <fallback-models>
       <model>z-ai/glm-4.7-flash</model>
       <model>openai/gpt-4o-mini</model>
     </fallback-models>
     <routing>price</routing>
     <max-input-price-per-million>0.20</max-input-price-per-million>
     <max-output-price-per-million>0.75</max-output-price-per-million>
     <require-structured-output>true</require-structured-output>
     <max-batch-articles>20</max-batch-articles>
     <max-input-tokens>30000</max-input-tokens>
     <request-timeout-seconds>90</request-timeout-seconds>
     <max-attempts>3</max-attempts>
     <max-split-depth>6</max-split-depth>
     <cache-enabled>true</cache-enabled>
   </llm>
   ```

2. Change `LLMConfig` and `RunConfig` defaults to OpenRouter plus the recommended chain.
   Update `configs/config.xml.example`, README, AGENTS, live-test defaults, and tests in the
   same branch. Never rewrite the user's populated ignored config.
3. Keep provider choice, primary model, ordered fallbacks, routing strategy, price caps,
   and structured-output requirement configurable through parsed XML; do not hard-code
   them inside the OpenRouter adapter.
4. Build the OpenRouter body with strict `json_schema`, `require_parameters=true`, native
   `models` fallbacks, and native `max_price`. Merge these fields with existing
   `extra_body` without accidentally replacing `provider.require_parameters`.
5. Verify the actual response model is one of the configured models. Record it for cost
   metrics and cache diagnostics. If it is not in the allowlist, reject the batch.
6. Use a pure batch-planning function so all boundaries are testable without providers.
7. Use typed provider adapters with a shared error classification.
8. Inject sleeper and random-jitter source. Production uses real implementations; tests
   use deterministic fakes.
9. Use a new `llm_batch_cache` table rather than altering article tables.
10. Cache exact complete batch responses only. Do not implement per-article summary caching
   because `rank-reasoning` and executive summaries can depend on the whole batch.
11. Preserve the current final combination: `summaries` plus joined `exec_summary` when
   present.
12. Keep batches sequential initially. Parallel provider calls require an additional rate-
   limit design and are not authorized by this branch.

### Cost guardrail

Add a deterministic test calculating maximum provider attempts for a configured number of
articles. It must prove attempts are finite. The implementation may never use an unbounded
retry loop or recursively split without a depth limit.

### Definition of done

Tests cover OpenRouter default/fallback routing and the direct-Gemini compatibility adapter
through fakes, every retry class, price caps, cache identity, corrupt cache, split recovery,
dry run, and final JSON shape. The full gate passes.

---

## Issue 10 — Validate all configuration and fix UTC conversion

Branch: `codex/10-config-validation-utc`

### RED tests

Parameterize invalid configuration tests for:

- `limit <= 0`;
- `max-age-hours <= 0` when present;
- `max-article-length <= 0`;
- `concurrency <= 0`;
- unsupported extractor;
- unsupported embedding provider;
- unsupported LLM provider;
- empty model names;
- malformed OpenRouter model slugs, duplicate fallback models, or more than three fallback
  models;
- unsupported OpenRouter routing strategy;
- nonpositive or nonsensical OpenRouter input/output price caps;
- a configured default/fallback model whose known committed catalog price exceeds the
  configured cap (configuration warning only; the native runtime price cap remains the
  enforcement authority because catalog prices can change);
- prefilter threshold outside `[0, 1]`;
- cluster size or candidate multiplier <= 0;
- HTTP timeouts, retry counts, byte limits, or per-host concurrency outside allowed bounds;
- LLM batch limits, token limits, timeouts, attempts, or split depth outside allowed bounds;
- malformed booleans such as `tru` instead of silently treating them as false;
- summary enabled without a prompt;
- email recipient configured without any possible sender, if that can be determined without
  reading secrets.

Also test:

1. A feed timestamp converts identically under `UTC`, `Europe/London`, and
   `America/New_York` process timezones.
2. Missing feed dates remain the minimum UTC datetime and sort last.
3. Validation happens before parsing feeds, opening a database, starting threads, or
   creating provider clients.

### GREEN design

1. Centralize strict boolean, positive integer, bounded float, enum, and nonempty-string
   parsing helpers in `config.py`.
2. Raise errors naming the exact XML path and invalid value, without including secrets or
   connection strings.
3. Validate the complete `AppConfig` before constructing `RunConfig` or external clients.
4. Replace `time.mktime` with `calendar.timegm` for feedparser UTC `struct_time` values.
5. Preserve existing defaults for omitted valid settings.

### Definition of done

Every invalid case produces exit code 1 through CLI tests and performs zero external calls.
Timezone tests are deterministic and restore the process timezone after themselves. The
full gate passes.

---

## Issue 11 — Slim and harden dependency/container delivery

Branch: `codex/11-runtime-container`

### Desired behavior

The production image should not contain pytest, mypy, Ruff, pre-commit, virtualenv, or
compiler toolchains. Tokenizer data needed at runtime should already be present. The image
must still support both configured extractors, FastEmbed, both LLM providers, SQLAlchemy,
templates, and email.

### RED verification

Before changes, record without committing generated artifacts:

```bash
docker build -t rss-morning:before .
docker image inspect rss-morning:before --format '{{.Size}}'
docker run --rm --network none rss-morning:before --help
```

If Docker is unavailable, record the blocker and do not claim this issue complete until CI
or an approved environment performs equivalent checks.

Add a container smoke script or CI step that asserts:

1. `python main.py --help` works with network disabled.
2. Importing every runtime module works.
3. Token truncation works with network disabled.
4. The runtime user is not root.
5. Development tools are absent from the final environment.
6. A hermetic config/snapshot dry run works without sockets or credentials.

### GREEN design

1. Maintain a small direct-dependency input file and reproducibly generated pinned runtime
   and development lock files. Do not hand-delete transitive packages without regenerating
   and testing the lock.
2. Keep `requirements.txt` as the documented runtime install for compatibility.
3. Keep `requirements-dev.txt` as runtime plus test/lint/type tooling.
4. Change CI to install development requirements.
5. Use a multi-stage Docker build:
   - builder installs compilation tools and builds wheels;
   - final stage installs wheels without compilers;
   - final stage copies only application runtime files;
   - tokenizer cache is prewarmed during build;
   - cache paths are readable/writable by the non-root user as documented.
6. Preserve FastEmbed cache volume behavior.
7. Add OCI labels and a Docker health/smoke command only if it does not contact external
   services.

### Acceptance measurements

Record before/after image byte sizes and installed package lists in the state file. The
final image must be smaller and must not contain the named development tools or build
packages. Do not set an arbitrary flaky percentage threshold.

### Definition of done

Both the regular full gate and all offline container smoke checks pass. README setup and
Docker sections are exact.

---

## Issue 12 — Add safe operational metrics

Branch: `codex/12-operational-metrics`

### Desired behavior

One run should reveal where time and money are spent without exposing private content.
Metrics must not alter stdout JSON.

### RED tests

With a fake monotonic clock and fake boundaries, assert INFO logs or a returned internal
stats object contain:

1. configured feed count, successful feed count, and failed feed count;
2. entries selected before and after URL deduplication;
3. article-cache hits, misses, retried null-content rows, successful extractions, and
   extraction failures;
4. page request count and transferred byte count;
5. prefilter input count, metadata candidates, full-text candidates, representatives, and
   clustered duplicates;
6. embedding cache hits/misses and provider/model duration without text;
7. LLM planned batches, provider calls, retries, split recoveries, exact-cache hits,
   submitted token estimate, and provider token usage when supplied;
8. email attempted/sent/failed;
9. duration for feed, extraction, prefilter, summary, email, and total stages;
10. no prompt, article text, API key, connection string, response body, or full email body
    appears at INFO.

### GREEN design

1. Add a private `RunStats` dataclass or small event collector.
2. Use `time.monotonic`, injected in tests.
3. Have boundaries report counts through typed results rather than scraping log strings.
4. Emit one concise stage-completion log per stage and one final run summary.
5. Keep stdout exclusively the existing JSON result.
6. Use provider usage metadata when available. Do not estimate output tokens by logging or
   retaining provider response text.
7. Metrics failure must never fail the digest.

### Definition of done

Tests prove metrics are accurate for mixed success/failure fixtures and prove secret/content
absence. The full gate passes.

---

## Issue 13 — Final integration, documentation, and release proof

Branch: `codex/13-final-integration-proof`

This branch contains no new feature work. It only resolves integration defects, completes
documentation, and records proof.

### Step 1: Audit documented contracts

Compare code, tests, `python main.py --help`, README, AGENTS, and example config. Update:

- the runtime-flow diagram;
- configuration defaults and valid ranges;
- cache behavior and versioning;
- HTTP retry/timeout/per-host behavior;
- two-stage prefilter mode and compatibility default;
- query-embedding format and clustering semantics;
- LLM batching, retry, exact cache, and dry-run behavior;
- the default OpenRouter model chain, configurable fallbacks, routing strategy, strict
  structured-output requirement, price caps, price-snapshot date, and direct-Gemini
  compatibility option;
- Docker cache/prewarm behavior;
- operational metrics;
- known-gaps lists, removing only gaps actually fixed.

Do not copy private config values into documentation.

### Step 2: Run the complete hermetic matrix

Run from a clean checkout of the integration tip, first with Python 3.11 and then Python
3.12:

```bash
python -m pip install -r requirements-dev.txt
make check
```

Also run:

```bash
python main.py --help
docker build -t rss-morning:final .
docker run --rm --network none rss-morning:final --help
```

The state file must record tool versions, commands, exit codes, 100% coverage, and final
image size.

### Step 3: Run deterministic cost-regression proofs

Using only synthetic fixtures and fakes, demonstrate:

1. N articles cause one article-cache read and at most one cache write.
2. A conditional 304 transfers zero feed-body bytes.
3. Compatible precomputed queries cause zero query-embedding calls.
4. Semantic duplicates cause fewer LLM input articles.
5. Metadata-first mode causes fewer page downloads than full-text mode on the defined
   fixture.
6. An exact cached LLM batch causes zero provider calls.
7. The default OpenRouter request contains the expected ordered model chain, strict
   structured-output requirement, and input/output price caps.
8. A fake primary-model outage selects an allowed fallback and attributes token usage and
   calculated cost to the actual returned model.
9. Retry worst-case call counts remain bounded.

These should be normal tests, not an informal script.

### Step 4: Live end-to-end approval gate

The live test uses external feeds, page downloads, FastEmbed model data, and OpenRouter and
may expose prompt/article content at DEBUG. Do not run it silently. Ask the operator for
explicit approval immediately before:

```bash
make live-e2e
```

If approval or credentials are unavailable, mark the final plan `BLOCKED ON LIVE E2E`.
Do not claim that all work is complete. All hermetic work may remain ready for review.

If the live test fails:

1. save only non-secret error summaries in state;
2. identify the responsible issue branch;
3. create `codex/<issue>-live-fix` from the integration branch;
4. reproduce the problem with a hermetic failing test;
5. make it green and merge normally;
6. rerun the entire gate before another live attempt.

Never patch directly from a live log without a hermetic regression test.

### Step 5: Final review

Run:

```bash
git status --short
git log --graph --oneline --decorate --all -40
git diff --check <original-commit>..HEAD
git diff --stat <original-commit>..HEAD
```

Confirm:

- only intended source, test, dependency, CI, config-example, and documentation files are
  changed;
- no ignored/private data is staged;
- no secrets appear in the diff;
- every issue has a RED test commit and GREEN implementation commit;
- every leaf branch was merged into `codex/stability-cost-speed`;
- 100% line and branch coverage still passes at the cumulative tip.

## 8. Stop conditions and recovery rules

Stop immediately and write the blocker into state when any of these occurs:

1. A required file has overlapping user changes.
2. A test unexpectedly accesses the network.
3. A change requires reading a private config, prompt, feed list, database, or credential.
4. Achieving green seems to require changing the public JSON shape.
5. A proposed database migration would overwrite or destructively rewrite existing rows.
6. Retry behavior cannot be proven bounded.
7. Coverage can reach 100% only by excluding ordinary production code.
8. Python 3.11 and 3.12 behavior differs and the cause is unknown.
9. The live test needs external spending and approval has not been given.
10. The branch contains unrelated modifications.

When stopped, do not attempt destructive cleanup. Leave the working tree intact, record the
exact command, error, current branch, current commit, and next safe action. Ask the operator
one focused question.

## 9. Final handoff format

The implementing agent's final response must include:

1. the integration branch name and tip SHA;
2. a one-line outcome for each issue 00–13;
3. exact verification commands and results;
4. final statement and branch coverage percentages, both 100%;
5. Python 3.11 and 3.12 results;
6. Docker before/after byte sizes and offline smoke result;
7. live end-to-end result, or the explicit approval/credential blocker;
8. a statement that stdout JSON contracts and partial-failure behavior were preserved;
9. a statement that no private/ignored user data was modified or committed;
10. any residual risk that could not be proven hermetically.

Do not say “done” if any gate is skipped, failing, or blocked. Use “ready for review, blocked
on <gate>” instead.

## 10. Why this order must not be changed

- Issue 00 prevents later branches from quietly reducing test confidence.
- Issues 01–03 repair correctness and make cache/database behavior safe before concurrency
  and network code is reorganized.
- Issue 04 creates the common HTTP seam needed by conditional feed caching.
- Issue 05 depends on that HTTP response metadata and batched database pattern.
- Issues 06–07 make embedding identity and query data trustworthy before the pipeline uses
  embeddings to avoid page downloads.
- Issue 08 depends on correct cache identity and real clustering.
- Issue 09 can then consume the smallest trustworthy candidate set and safely cache exact
  LLM batches.
- Issue 10 validates every setting introduced by prior branches in one centralized pass.
- Issue 11 packages the final dependency and tokenizer behavior rather than an intermediate
  design.
- Issue 12 instruments stable interfaces rather than interfaces still being rewritten.
- Issue 13 proves the cumulative system rather than treating individually green branches as
  sufficient evidence.

If a cheaper or smaller-context agent follows this order, updates durable state, and obeys
the merge and coverage gates, it can stop and resume without guessing what happened and
without allowing one optimization to silently invalidate another.
