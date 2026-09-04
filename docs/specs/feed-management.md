# Feature: RSS Podcast Downloader — Feed Management & Date-Based Sync

## Specification: docs/specs/feed-management.md

## Problem Statement

The tool currently requires the full RSS feed URL and save directory on every
invocation, and `--num-episodes N` naively takes the N most recent episodes in
feed order rather than N episodes *after the last one already downloaded*. Four
new capabilities make the tool behave like a proper "set and forget" podcatcher.

## Background

The local SQLite DB (`downloads.db`) already tracks feeds (table `feeds`:
`feed_id`, `feed_url`, `feed_title`) and downloaded episodes (table `episodes`:
`episode_id`, `feed_id`, `guid`, `title`, `published`, `filepath`,
`downloaded_at`, with `UNIQUE (feed_id, guid)`). This means:

- We can enumerate and select feeds without re-typing URLs.
- We can compute "everything published strictly after the newest episode we
  already have" to drive incremental syncs.

All date handling should reuse the existing helpers so filename prefix and
recency ordering agree: `_entry_datetime(entry)`, `_published_parsed_as_date`,
`entry_date_prefix`, `_episode_sort_key`.

## Requirements

- [ ] REQ-1: `--list-feeds` — print every feed in the DB (`feed_id | feed_url | feed_title`),
      or `No feeds found in database.`
- [ ] REQ-2: `--feed-id N` — pull a feed whose URL is already stored in the DB by
      its `feed_id`, so the URL positional argument is not required.
- [ ] REQ-3: Incremental `--num-episodes`:
      - If a feed has episodes already downloaded, `--num-episodes N` downloads
        the N episodes **published strictly after the newest downloaded one**,
        not "the N newest overall".
      - If NO episodes have been downloaded yet for the feed, `--num-episodes N`
        falls back to the N newest episodes overall.
      - If `--num-episodes` is omitted, download **all** episodes published after
        the newest downloaded one (incremental catch-up), never re-downloading
        the whole history once synced.
- [ ] REQ-4: `--keep-last` — after a run for a feed, prune the DB so only the
      single newest episode remains (as a sync reference), deleting older rows
      and their on-disk files.
- [ ] REQ-5: Existing behavior preserved when none of the new flags are used
      (URL + save_dir still work); new flags are opt-in.
- [ ] REQ-6: Add tests; keep ruff clean; all existing tests still pass.

### Scenarios

```gherkin
Feature: Incremental sync
  Scenario: num-episodes counts from newest downloaded
    Given feed has episodes E1..E20 published 2024-01-01..2025-12-31
    And the DB already contains episodes up to 2025-06-15
    When --num-episodes 5 is used
    Then exactly the 5 episodes published after 2025-06-15 are considered

  Scenario: fewer than N newer episodes exist
    Given only 3 episodes are published after the newest downloaded
    When --num-episodes 10 is used
    Then those 3 episodes are considered (not 10)

  Scenario: fresh feed keeps old behavior
    Given no episodes are in the DB for this feed
    When --num-episodes 5 is used
    Then the 5 newest episodes overall are considered

  Scenario: no limit means incremental catch-up
    Given episodes already downloaded up to 2025-06-15
    When --num-episodes is omitted
    Then all episodes published after 2025-06-15 are considered

Feature: Feed selection
  Scenario: list feeds
    When --list-feeds is used
    Then each feed is printed as "feed_id | feed_url | feed_title"

  Scenario: pull by stored id
    Given feed_id 2 exists in the DB
    When --feed-id 2 is used with a save dir
    Then the stored URL for feed_id 2 is fetched

Feature: keep last
  Scenario: prune to newest
    Given a feed has 5 downloaded episodes with files
    When --keep-last is used after a run
    Then only the newest episode row remains and its file survives
    And the other 4 rows and their files are deleted
```

## Architecture

### CLI
Extend the existing single positional URL handling:

- `--list-feeds`: boolean. When set, list feeds and exit (URL/save_dir optional).
- `--feed-id N`: integer, optional. Overrides/obviates the positional `rss_url`.
  Validation: exactly one of `rss_url` positional **or** `--feed-id` must be given
  (unless `--list-feeds`). If both given, `--feed-id` wins and the positional is
  treated as the save directory? — No. Keep it simple: `--feed-id` supplies the
  URL; `save_dir` is still a required positional. So positional args become
  `[rss_url]` (optional only when `--feed-id` used) + `save_dir` (required).
- `--keep-last`: boolean flag.

### parse_and_download changes (REQ-3)
Current flow: build `all_episodes` -> sort by `_episode_sort_key` desc -> if
`num_episodes`, slice first N -> filter out already-DB'd by guid -> download.

New flow:
1. Build `all_episodes` (audio/mpeg links).
2. Query DB for the newest `published` datetime already downloaded for this
   `feed_id`: `SELECT MAX(published) FROM episodes WHERE feed_id = ?`. Store as
   `last_downloaded`.
3. Order candidates by recency desc (stable).
4. Determine cutoff set:
   - Candidates with a resolvable `_entry_datetime` strictly greater than
     `last_downloaded` → these are the "new" candidates.
   - If `last_downloaded` is None (fresh feed), all candidates are "new".
5. Still apply the guid-based dedup (in case of gaps/edge cases where a newer
   episode exists but an older one was skipped).
6. If `num_episodes` is not None: among the new candidates take the first
   `num_episodes` (already newest-first). If None: take all new candidates.
7. Download those.

Notes:
- If the feed's newest episode is <= last_downloaded (no new content), the new
  set is empty → nothing downloads. Good (incremental catch-up).
- If `last_downloaded` is set but an episode row was manually removed, the guid
  dedup still prevents accidental re-download of an already-present guid.

### keep-last (REQ-4)
New helper `prune_to_keep_last(conn, feed_id, save_dir)`:
- Find newest row: `SELECT episode_id, filepath FROM episodes WHERE feed_id = ?
  ORDER BY published DESC, episode_id DESC LIMIT 1`.
- Delete all other rows for that feed; for each deleted row's `filepath`,
  `os.remove` if it exists and is under `save_dir`.
- Called after `parse_and_download` in `main()` when `--keep-last`.

### Feed helpers (REQ-1, REQ-2)
- `list_feeds(db_path=None)`: open DB (or default), print rows.
- `get_feed_url_by_id(conn, feed_id)`: return URL or None.
- `main()`: resolve `rss_url` from `--feed-id` if positional omitted.

## API / Interface (function signatures)

- `list_feeds(db_path=None) -> None`
- `get_feed_url_by_id(conn, feed_id) -> str | None`
- `_get_last_downloaded_date(conn, feed_id) -> datetime | None`
- `prune_to_keep_last(conn, feed_id, save_dir) -> None`
- `parse_and_download(...)` — internal logic only; signature unchanged.
- `main()` — parses new flags and wires routing.

## Testing Strategy

Unit tests (`tests/unit/`):
- `list_feeds` prints DB rows; empty DB prints "No feeds found".
- `get_feed_url_by_id` returns URL / None.
- Date cutoff: newest-downloaded filter logic returns correct new set for
  fresh / partially-synced / fully-synced feeds. Since this is inside
  `parse_and_download` (needs feed + DB), factor the pure decision into a small
  testable helper, e.g. `_select_candidates(all_episodes, last_downloaded,
  num_episodes) -> list[entry]` that returns entries to consider. Unit-test that.
- `prune_to_keep_last` keeps newest row, deletes older rows + files.

Integration tests (`tests/integration/`):
- End-to-end: local HTTP feed with episodes at known dates; first run downloads
  episode D; second run with `--num-episodes 2` downloads the next 2 after D and
  not earlier ones; omit `--num-episodes` on a third run and assert no re-download.
- `--list-feeds` and `--keep-last` CLI flows.

## Out of Scope

- Packaging / type hints / console entry point.
- Adding, deleting, or renaming feeds via CLI (only listing and pulling existing).
- Playing/downloading non-MP3 enclosures beyond current audio/mpeg handling.
