# Feature: CRUD Feeds + OPML Import/Export

## Problem Statement
Feeds can only be added implicitly and listed. There is no way to delete a feed, export the collection for backup/migration, or import OPML from other podcatchers (AntennaPod, Pocket Casts) without manually editing `downloads.db`.

## Background
- DB: `feeds(feed_id, feed_url UNIQUE, feed_title, save_dir)`, `episodes(feed_id FK, guid UNIQUE per feed, filepath)`
- `list_feeds()` already exists; `get_or_create_feed`, `get_feed_url_by_id`, `get_feed_save_dir` exist.
- OPML is standard: `<opml><body><outline type="rss" xmlUrl="..." text="..." /></body></opml>`

## Requirements
- **REQ-1: `--remove-feed ID`** — deletes feed and its episodes from the DB; files under `save_dir` are only deleted if `--delete-files` is also passed. Without the flag, rows are deleted but files remain orphaned (logged). Clear error if ID does not exist.
- **REQ-2: `--export-opml FILE`** — dumps all feeds to OPML 2.0 (includes `xmlUrl`, `text`/`title`, optional `htmlUrl`). Creates/overwrites FILE. Compatible with external import.
- **REQ-3: `--import-opml FILE`** — reads OPML, for each `outline` with `xmlUrl` creates the feed if it does not exist (uses `text`/`title` as `feed_title`). Reports how many new vs already existing. Does not download episodes.
- **REQ-4: CLI composition** — these flags are exclusive operations: they do not require `rss_url`/`save_dir` and do not download. If combined with other sync flags, they execute and return. `--list-feeds` already follows this pattern.
- **REQ-5: `--delete-files` only valid with `--remove-feed`** — validated via `parser.error`.

### Scenarios
```gherkin
Feature: remove feed
  Scenario: delete feed 2 without files
    Given feed 2 with 3 episodes
    When --remove-feed 2
    Then feed 2 and its 3 rows disappear; files remain on disk

  Scenario: delete with --delete-files
    When --remove-feed 2 --delete-files
    Then files under save_dir are also deleted (if they exist and are under save_dir)

Feature: OPML
  Scenario: export
    When --export-opml feeds.opml
    Then FILE contains <outline> per feed with xmlUrl

  Scenario: import
    Given OPML with 2 urls, one already exists
    When --import-opml feeds.opml
    Then 1 new feed created, 1 skipped
```

## Architecture
### Helpers
- `remove_feed(conn, feed_id, save_dir=None, delete_files=False) -> bool` — deletes episodes and feed; if delete_files and save_dir given, deletes files with `commonpath` guard + shared-kept protection not needed (whole feed is deleted).
- `export_opml(db_path, output_path)` — query `SELECT feed_url, feed_title FROM feeds`, generates XML with `xml.etree.ElementTree`, writes UTF-8 with header `<?xml ...>` and `<opml version="2.0">`.
- `import_opml(db_path, input_path, conn=None) -> (imported, skipped)` — parses OPML, iterates `outline` recursively, extracts `xmlUrl`, `text`/`title`, calls `get_or_create_feed` (detects skip via existing URL).

### CLI
- `parser.add_argument('--remove-feed', type=int)`, `'--export-opml'`, `'--import-opml'`, `'--delete-files' action='store_true'`.
- Validation: `--delete-files` requires `--remove-feed`.
- Order in `main()`: after parsing `list_feeds`, check `remove_feed` → execute and return; `export/import` similar; only if none of these operations runs, proceed to normal download flow.

## API / Interface
- `remove_feed(conn, feed_id, delete_files=False) -> bool`
- `export_opml(db_path=None, output_path)` / `import_opml(db_path=None, input_path)`
- CLI flags as above.

## Testing Strategy
- Unit: `remove_feed` without/with delete_files; export/import round-trip; import OPML with duplicates; error cases.
- Integration: create 2 feeds, export, delete DB, import, verify feeds return.

## Out of Scope
- Updating URL of an existing feed (done via remove+re-add).
- OPML with complex nested categories — flattened.
