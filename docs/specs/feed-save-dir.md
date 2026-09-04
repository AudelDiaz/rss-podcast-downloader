# Feature: Per-Feed Save Directory (save_dir)

## Specification: docs/specs/feed-save-dir.md

## Problem Statement

Today the download destination folder is not stored on the `feeds` table. It is
re-supplied on every CLI invocation as the `save_dir` positional and survives only
indirectly through each episode's `filepath`. This makes a true folder-free
workflow impossible: `--feed-id N` still requires the folder to be typed each time,
and `--list-feeds` can't show where each feed saves.

## Background

- `feeds` currently has columns: `feed_id`, `feed_url`, `feed_title`.
- `episodes.filepath` stores the full absolute path per episode, so the destination
  folder is recoverable from it (dirname of the newest episode).
- `setup_database()` already performs lightweight in-place migrations (it can
  `ALTER TABLE`); `CREATE TABLE IF NOT EXISTS feeds` does NOT add a column to an
  existing table, so a dedicated `ALTER TABLE ... ADD COLUMN` is required for
  existing databases.
- This project is a standalone script; no packaging / no `[project]` table.

## Requirements

- [ ] REQ-1: Add a `save_dir TEXT` column to `feeds`.
- [ ] REQ-2: Fresh databases create `feeds` with `save_dir`; existing databases get
      the column via an idempotent `ALTER TABLE ... ADD COLUMN` (no-op if present).
- [ ] REQ-3: Back-fill migration: for any feed that has episodes but no `save_dir`,
      set `save_dir` to the directory of its most recently downloaded episode
      (highest `episode_id`) — recovering existing folders from `filepath`.
- [ ] REQ-4: When a download runs and a `save_dir` is provided, persist it for that
      feed (normalized to an absolute path). If the feed already exists, update the
      stored value; if created fresh, store it at creation.
- [ ] REQ-5: `--feed-id N` with NO positional save_dir uses the feed's stored
      `save_dir`; if the feed has none stored yet, error with a clear message.
- [ ] REQ-6: `--list-feeds` includes the stored `save_dir` per feed.
- [ ] REQ-7: `--keep-last` pruning continues to work (it uses the resolved save_dir).
- [ ] REQ-8: Backward compatibility: the classic `URL save_dir` invocation still
      works and records the folder.

### Scenarios

```gherkin
Feature: Stored save directory
  Scenario: existing DB gains save_dir column
    Given a database whose feeds table lacks save_dir
    When setup_database runs
    Then feeds has a save_dir column
    And feeds that have episodes have save_dir back-filled to their newest folder

  Scenario: download persists the folder
    Given a feed
    When a run downloads with save_dir=/a/b
    Then the feed's save_dir becomes /a/b (absolute)

  Scenario: feed-id needs no folder once stored
    Given feed 2 has save_dir stored
    When `--feed-id 2` runs with no save_dir positional
    Then the stored folder is used

  Scenario: feed-id without stored folder errors
    Given feed 3 has no save_dir stored
    When `--feed-id 3` runs with no save_dir positional
    Then a clear error is printed and no download starts
```

## Architecture

### Schema / migration (in `setup_database`)
1. `CREATE TABLE IF NOT EXISTS feeds (...)` now includes `save_dir TEXT` (so fresh
   DBs are correct from the start).
2. After table creation, read `PRAGMA table_info(feeds)`; if `save_dir` is absent,
   `ALTER TABLE feeds ADD COLUMN save_dir TEXT`.
3. Back-fill: for each feed whose `save_dir` is NULL/empty and which has at least
   one episode, set `save_dir = dirname(filepath)` of the episode with the highest
   `episode_id` for that feed.
4. Commit once at the end (existing pattern).

### Feed helpers
- `get_feed_save_dir(conn, feed_id) -> str | None`
- `set_feed_save_dir(conn, feed_id, save_dir) -> None` — normalizes to `os.path.abspath`.
- Extend `get_or_create_feed(conn, feed_url, feed_title, save_dir=None)` to store
  `save_dir` at insert, or add an explicit update path after the feed id is known.

### main() resolution order for save_dir
1. If `save_dir` positional provided → use it, and persist via
   `set_feed_save_dir` after the feed id is known (covers fresh + existing feeds).
2. Else if `--feed-id` given → look up stored `get_feed_save_dir`; use if found;
   else print a clear error and exit.
3. Else (URL positional, no folder) → error (existing behavior), telling the user
   the folder is required the first time.

### list_feeds output
Print `feed_id | feed_url | feed_title | save_dir`.

## API / Interface (function signatures)

- `setup_database(db_path=None)` — schema/migration as above.
- `list_feeds(db_path=None) -> None` — now includes save_dir column.
- `get_feed_url_by_id(conn, feed_id) -> str | None` — unchanged.
- `get_feed_save_dir(conn, feed_id) -> str | None` — new.
- `set_feed_save_dir(conn, feed_id, save_dir) -> None` — new.
- `get_or_create_feed(conn, feed_url, feed_title, save_dir=None)` — stores folder.
- `main()` — resolution order above.

## Testing Strategy

Unit (`tests/unit/`):
- Fresh DB: `feeds` has `save_dir` column.
- Existing DB lacking the column: migration adds it (idempotent on second call).
- Back-fill: feed with episodes gets save_dir = dirname of newest filepath.
- `get_feed_save_dir` / `set_feed_save_dir` round-trip; normalization to abs path.
- `get_or_create_feed(..., save_dir)` stores it on insert.
- `list_feeds` prints save_dir.

Integration (`tests/integration/`):
- A run with a real/local feed persists the folder and a later `--feed-id` (no
  folder) resolves it.

## Out of Scope

- Moving/migrating already-downloaded files to a new folder.
- Multiple save dirs per feed.
- Packaging / type hints.
