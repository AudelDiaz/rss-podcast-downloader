# Feature: Flexible Retention (--keep / --max-age / --max-size)

## Problem Statement
Today only `--keep-last` exists (keep 1 episode as sync reference). For "set and forget" usage the disk fills up; users need "last N", "delete >30 days" or "do not exceed 2GB per feed" without manually editing the DB.

## Background
- `prune_to_keep_last(conn, feed_id, save_dir)` keeps the newest (`ORDER BY published DESC, episode_id DESC`), deletes rows and files under `save_dir`, protects shared files across rows.
- `episodes.published` format `YYYY-MM-DDTHH:MM:SS` or `''` (dateless). `episodes.filepath` absolute.
- Existing flags: `--keep-last`, `--num-episodes`, `--since`, `--all`. New flags must compose with them and respect `--dry-run`.

## Requirements
- **REQ-1: `--keep N`** — after download, keep only the N newest episodes (N>=1). Replaces/augments `--keep-last` (`--keep-last` ≡ `--keep 1`, both mutually exclusive).
- **REQ-2: `--max-age DAYS` or `30d`/`30`** — delete episodes with `published` strictly before `now - DAYS`. Dateless (`published=''`) is preserved (not evaluable). Orphan files outside `save_dir` are never deleted.
- **REQ-3: `--max-size SIZE`** — per-feed quota: keep newest first, accumulate `os.path.getsize(filepath)` until exceeding `SIZE` (e.g. `500M`, `2G`, `1024`). The rest is purged. If a file does not exist, size 0 but the row is still deleted.
- **REQ-4: Composition** — filters apply in order: `keep` → `max-age` → `max-size` on the ordered set. All purges share shared-file protection and `commonpath(save_dir)`.
- **REQ-5: Dry-run** — with `--dry-run` no DB is written and no files are deleted; log what would be purged.
- **REQ-6: Backward compat** — `--keep-last` keeps working; if both `--keep` and `--keep-last` are passed, show a clear error.

### Scenarios
```gherkin
Feature: keep N
  Scenario: keep 2 of 5
    Given 5 episodes 2020..2024
    When --keep 2
    Then the 2 newest remain, 3 rows and their files are deleted

Feature: max-age
  Scenario: delete >30 days
    Given today 2025-06-15, episodes 2025-05-01 and 2025-06-10
    When --max-age 30
    Then only 2025-06-10 survives; dateless survives

Feature: max-size
  Scenario: 1KB quota
    Given 3 files 600B each (new, middle, old)
    When --max-size 1KB
    Then only the newest (600B) remains; the others are purged
```

## Architecture
### Helpers
- `parse_size(value: str) -> int` — parses `"500M"/"2G"/"1024"` → bytes (K=1024, M, G). Case-insensitive, accepts optional `B` suffix.
- `parse_max_age(value: str) -> timedelta` — accepts `"30"`, `"30d"`, `"30days"` → `timedelta(days=30)`.
- `prune_feed(conn, feed_id, save_dir, keep=None, max_age=None, max_size=None, dry_run=False)` — testable core. Returns `(kept, removed)`. Internally:
  1. `SELECT episode_id, filepath, published FROM episodes WHERE feed_id=? ORDER BY published DESC, episode_id DESC`
  2. Determine initial `to_keep` (first `keep` rows if `keep` else all).
  3. Filter `max_age`: for each row in `to_keep`, if `published` parseable and `published < cutoff` → move to `to_remove` (dateless never moved by age).
  4. Filter `max_size`: iterate `to_keep` in order, accumulate sizes, cut when `acc > max_size`.
  5. Protect shared paths, delete with `commonpath` guard.

- `prune_to_keep_last(...)` becomes wrapper `prune_feed(..., keep=1)`.

### CLI
- `--keep int >=1`, `--max-age str`, `--max-size str`, validated in `main()` via parsers; error with `parser.error` if invalid.
- Order in `main()`: download → `prune_feed` if any flag present; `--dry-run` propagates to `prune_feed`.

## API / Interface
- `prune_feed(conn, feed_id, save_dir, keep=None, max_age=None, max_size=None, dry_run=False) -> tuple[int,int]`
- `prune_to_keep_last(conn, feed_id, save_dir)` — compat wrapper.
- `parse_size(s: str) -> int`, `parse_max_age(s: str) -> timedelta`.

## Testing Strategy
- Unit: `keep N` keeps N newest; `max-age` respects dateless; `max-size` with files of known size; `keep+max-age` composition; `shared file` not deleted if kept row references it; `dry_run` does not delete.
- Integration: full flow with HTTP fixture + real files of different sizes.

## Out of Scope
- Global multi-feed quota.
- Moving files to another directory when purging.
