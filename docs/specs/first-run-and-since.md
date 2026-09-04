# Feature: First-Run Policy & Date-Window Sync

## Specification: docs/specs/first-run-and-since.md

## Problem Statement

On a feed with **no prior downloads** (new feed, `last_downloaded is None`),
`_select_candidates` returns the ENTIRE feed history, so running with no
`--num-episodes` silently attempts to download the whole archive (potentially
hundreds of episodes / gigabytes). There is also no way to seed a new feed from a
chosen date rather than a count.

## Background

- Selection logic lives in `_select_candidates(all_episodes, last_downloaded,
  num_episodes)`.
- When `last_downloaded is None`, `candidates = ordered` (all episodes).
- `parse_and_download` calls `_select_candidates` after computing
  `last_downloaded = _get_last_downloaded_date(conn, feed_id)`.
- CLI flags today: `--feed-id`, `--list-feeds`, `--keep-last`, `--save_text`,
  `--num-episodes`; positionals `[rss_url] [save_dir]`.
- Incremental forward-sync (from newest downloaded) is the established model.

## Requirements

- [ ] REQ-1: New `--since YYYY-MM-DD` flag — only episodes published **on or
      after** that date are candidates. Applies on any run; combined with
      `--num-episodes` it narrows the window then caps the count.
- [ ] REQ-2: New `--all` flag — explicit full-archive download for a new feed
      (all episodes, or all episodes not already owned).
- [ ] REQ-3: New-feed guard — when a feed has NO prior downloads AND none of
      `--num-episodes`, `--since`, or `--all` is given, download **nothing** and
      print a clear message telling the user to pass one of those controls.
- [ ] REQ-4: Once a feed has prior downloads, `--num-episodes`/`--since`/no-flag
      behave as today (forward incremental from newest downloaded). `--since` may
      be earlier than the newest downloaded only if the feed owns nothing newer;
      generally the incremental anchor still applies.
- [ ] REQ-5: The incremental cutoff (episodes published strictly after the newest
      already-downloaded) still applies so nothing already owned is re-downloaded,
      and no gap is left behind an unowned older episode on subsequent runs.
- [ ] REQ-6: Update module docstring + `--help`; keep ruff clean; add tests.

### Scenarios

```gherkin
Feature: First run of a new feed
  Scenario: no controls given
    Given a feed with no episodes in the DB
    When no --num-episodes/--since/--all is passed
    Then nothing downloads and a clear message is shown

  Scenario: newest N requested
    Given a new feed
    When --num-episodes 10 is passed
    Then the 10 newest episodes are candidates

  Scenario: date window requested
    Given a new feed
    When --since 2026-01-01 is passed
    Then only episodes published on/after 2026-01-01 are candidates

  Scenario: full archive explicitly requested
    Given a new feed
    When --all is passed
    Then the entire history is candidates

Feature: Established feed (has prior downloads)
  Scenario: incremental after owning newest
    Given the feed already owns its newest episode
    When a normal run happens
    Then nothing new downloads (already caught up)

  Scenario: since older than newest owned
    Given the feed owns an episode published 2026-05-05
    When --since 2026-01-01 is passed
    Then nothing downloads (feed already owns everything from 2026-01-01 onward is not assumed;
         candidates are only episodes after newest owned — i.e. none newer than 2026-05-05)
```

## Architecture

### New pure decision helper
Refactor candidate selection into a single testable function:

```python
def _select_candidates(
    all_episodes,
    last_downloaded=None,   # datetime or None
    num_episodes=None,      # int or None
    since=None,             # datetime or None
    allow_full_history=False,  # True when --all given (new feed override)
) -> list
```

Logic order:
1. Sort newest-first.
2. If `last_downloaded is not None`: base set = episodes with `dt is None or
   dt > last_downloaded` (existing incremental; nothing owned is re-fetched).
3. Else (new feed):
   - if `allow_full_history` (--all): base set = all episodes.
   - else: base set = all episodes (still gated below by num/since; if none
     given, returns [] → the "no control" guard is a *caller* decision).
4. Apply `since`: keep episodes with `dt is None or dt >= since`.
5. Apply `num_episodes`: first N (if `>= 1`).
6. Return.

### New-feed guard (caller in parse_and_download)
Compute `is_new_feed = last_downloaded is None`. If `is_new_feed` and
`num_episodes is None and since is None and not args.all` → log a clear message
and return without downloading.

### parse_and_download signature
Add `since=None` and `full_history=False` parameters (internal wiring; called
from main). Keep default None/False so existing callers/tests are unaffected.

### main() / CLI
- `--since YYYY-MM-DD`: parse to datetime; invalid → parser.error with example.
- `--all`: store_true.
- Wire `since` and `all` into `parse_and_download`.

## API / Interface

- `_select_candidates(all_episodes, last_downloaded=None, num_episodes=None,
  since=None, allow_full_history=False)`
- `parse_and_download(save_dir, save_text, num_episodes=None, conn=None,
  feed_id=None, feed=None, session=None, since=None, full_history=False)`
- main: `--since`, `--all`.

## Testing Strategy

Unit (`tests/unit/`):
- New feed + no controls → select returns [] (guard decision asserted at the
  helper/caller boundary we expose) — assert the guard function/message.
- `--num-episodes` on new feed → newest N.
- `--since` window on new feed → episodes >= date.
- `--since` + `--num-episodes` → window then cap.
- `--all` on new feed → full history.
- Established feed: incremental anchor still honored; `--since` earlier than
  newest-owned yields nothing; dateless entries remain candidates.

Integration:
- A local feed; first run with no controls downloads nothing; `--since` seeded
  run downloads expected window; subsequent run downloads nothing.

## Out of Scope

- Backfill older-than-owned episodes (user declined for now).
- Packaging/type hints.
