# Feature: RSS Podcast Downloader — Code-Quality Hardening

## Problem Statement

`rss-podcast-downloader.py` is a functional but brittle single-file CLI. It works,
but several correctness issues and a lack of automated tests make it unsafe to
change. This spec addresses the correctness/quality findings (not packaging),
and adds a test harness so future edits are regression-safe.

## Background

The tool downloads podcast episodes from an RSS feed, deduplicates them in a
local SQLite DB (`feeds` + `episodes` tables), writes ID3 tags to MP3s, and
sanitizes titles into `YYYY-MM-DD_ascii_title.mp3` filenames. It is a
standalone-script project — per the `sdd` convention, code is regenerable from
this spec, so restructuring is not risky.

Standing up tests first matters because today nothing guards behavior: the
`content` param is dead, `--num-episodes` may slice the wrong end of the feed,
and date parsing silently drops the filename prefix for many feeds.

## Requirements

- [x] REQ-1: Remove the dead `content` parameter from `parse_and_download(...)`;
      fetch and feed data are already passed in.
- [x] REQ-2: Make `--num-episodes N` reliably select the N **newest** episodes,
      regardless of `feed.entries` order, instead of the first N in list order.
- [x] REQ-3: Broaden filename date parsing so feeds with common non-RFC822 date
      strings still get their `YYYY-MM-DD` prefix (reuse `published_parsed`
      when available; fall back to ISO-8601 / RFC-2822 via the stdlib).
- [x] REQ-4: Stream episode downloads to disk in chunks instead of buffering the
      whole file in memory via `response.content`; set a descriptive
      `User-Agent` header on feed and download requests.
- [x] REQ-5: Fix stale docstrings/module header that reference `ripper.py`.
- [x] REQ-6: Make the module importable for tests (keep `if __name__ ==
      "__main__":` guard) so pure functions can be unit-tested without running
      `main()`.
- [x] REQ-7: Add unit tests (`tests/unit/`) for `sanitize_title`, date parsing,
      DB dedup logic, and feed-ordering logic.
- [x] REQ-8: Add integration tests (`tests/integration/`) covering an end-to-end
      download against a local HTTP fixture server and a temp SQLite DB.
- [x] REQ-9: Configure pytest (unit on push, integration on PR) and `ruff`,
      including a GitHub Actions CI workflow.
- [x] REQ-10: Do NOT restructure into a package / module layout (out of scope).

### Scenarios

```gherkin
Feature: Filename sanitization
  Scenario: Newest-selection honors publication date
    Given a feed whose entries are NOT in publication order
    When --num-episodes 3 is used
    Then the 3 most recently published episodes are considered

  Scenario: Date prefix for non-RFC822 feed
    Given an episode whose published string is not RFC822
    And feedparser exposed published_parsed
    Then the filename gets a correct YYYY-MM-DD prefix

  Scenario: ASCII sanitization unchanged
    Given a title "Café au Lait & Ep. 5!"
    Then filename contains only [a-z0-9._-], lowercase, no diacritics
```

## Architecture

Single-file CLI remains a single file; functions are refactored so all pure,
testable logic takes explicit inputs and returns values (no hidden globals).
Download paths accept a `requests.Session` so tests can inject a mock/local
transport. A shared `sanitize_filename_from_entry(entry)` helper centralizes the
date + title logic currently duplicated between filename generation and the DB
record.

Concrete thresholds/branches to honor:
- `--num-episodes`: sort candidate episodes by `published_parsed` descending,
  then take the first N. `None` = all.
- Date: prefer `published_parsed` (struct_time) → format `%Y-%m-%d`. If absent,
  try the existing RFC822 formats, then an ISO-8601 fallback. If still unparsed,
  log a warning and emit a filename with no prefix (existing behavior).
- Streaming: `stream=True`, write `iter_content(chunk_size=8192)`; on request
  error, remove any partial file before retry/abort.

## API / Interface

- `download_file(url, filename, session, retries=3) -> bool` — streams to disk.
- `sanitize_title(title, date_str=None) -> str` — unchanged signature (kept for
  compat); now delegates date parsing to a shared helper.
- `sanitize_filename_from_entry(entry) -> str` — new: returns
  `YYYY-MM-DD_ascii.mp3` basename given a feedparser entry.
- `setup_database(db_path=None) -> sqlite3.Connection` — new optional param so
  tests use a temp DB instead of the script's `downloads.db`.
- `parse_and_download(save_dir, save_text, num_episodes=None, conn=None,
  feed_id=None, feed=None, session=None)` — `content` removed.

## Testing Strategy

- `tests/unit/`: pure-function tests (sanitize, date parse, ordering, dedup)
  run on every change — fast, no network/DB.
- `tests/integration/`: spin a `http.server`/`ThreadingHTTPServer` serving a
  canned feed + a small MP3; run the real download path against a temp DB;
  assert file exists, dedup skips re-download, tags written.
- Run: `uv run pytest tests/unit -v` and `uv run pytest tests/integration -v`.
- Lint: `uv run ruff check .` and `uv run ruff format --check .`.

## Out of Scope

- Packaging the project as a pip/uv module or console entry point.
- Type-hint annotations / mypy.
- Renaming or moving the entry script.
- Reaching into opencode MCP servers (ghost/drift) or the author's memory files.
