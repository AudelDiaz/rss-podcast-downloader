# Feature: RSS Podcast Downloader — Review Follow-ups (Post-PR #2)

## Context

PR #2 (`feature/stateful-download-tracking` → `main`, merged as `ba3b397`)
landed the code-quality hardening spec in
[`rss-hardening.md`](rss-hardening.md): REQ-1..10 are met, all tests pass, and
`ruff` is clean. During the adversarial review a handful of **non-blocking but
valuable** findings were logged. None are correctness blockers, but each is a
real robustness/maintainability gap worth closing. This spec plans that follow-up
work. Branch: `feature/review-followups`.

The findings below are ordered by value/risk. Each includes the file/line it was
observed at on `main` at merge time, a concrete fix, an acceptance test, and an
S/M size per the repo's right-sizing convention.

---

## FP-1 — Guard `Content-Length` parsing in `download_file`  (M)

**Where:** `rss-podcast-downloader.py` `download_file`, the `expected =
int(expected)` line near the `Content-Length` read.

**Problem:** A malformed or empty `Content-Length` header (e.g. a misconfigured
CDN sending `Content-Length: `) makes `int()` raise `ValueError`. `ValueError` is
**not** in the `except (requests.RequestException, OSError)` clause, so it escapes
`download_file`, propagates through `parse_and_download`, and is only swallowed by
`main()`'s catch-all — which aborts every remaining episode in the run and skips
the DB record for the current file. That defeats the function's whole purpose of
being a robust streamer.

**Fix:** Wrap the header parse defensively; treat an unparseable/absent length as
"no length declared" (no verification) rather than an error:

```python
expected = response.headers.get('Content-Length')
if expected is not None:
    try:
        expected = int(expected)
    except (TypeError, ValueError):
        expected = None  # can't verify; fall back to streaming-only success
```

**Acceptance test:** unit test with `headers = {'Content-Length': 'abc'}` whose
body is a valid short payload → `download_file` returns `True` and writes the
bytes (not an exception, not `False`). Mirror the existing fake-session pattern in
`tests/unit/test_db_and_selection.py`.

---

## FP-2 — Test the legacy-schema migration branch  (S)

**Where:** `rss-podcast-downloader.py` `setup_database` (the
`ALTER TABLE episodes RENAME TO episodes_old_pre_multi_feed` path).

**Problem:** The only DB test (`test_setup_database_fresh_db_does_not_archive`)
covers the *fresh-DB no-op*. The branch that actually **renames a legacy
`episodes` table lacking `feed_id`** is completely untested. Since a bug here
silently resets download history, it is the highest-value untested path in the DB
layer.

**Fix:** Add a test that creates a legacy `episodes` table *without* `feed_id`
(and with the old column set), runs `setup_database`, and asserts:
- the old table was renamed (exists as `episodes_old_pre_multi_feed`), and
- a new `episodes` table with `feed_id` exists, and
- calling `setup_database` a second time is idempotent (no re-archive).

---

## FP-3 — Cover the retry/backoff path of `download_file`  (S–M)

**Where:** `download_file` retry loop (`attempt`, exponential
`time.sleep(2**attempt)`).

**Problem:** Only single-attempt success/failure is tested. The retry-then-succeed
control flow and the final-failure path are unverified, and `time.sleep(2**attempt)`
would stall real tests (2/4/8 s).

**Fix:** Inject a `sleep_fn` parameter (default `time.sleep`) or mock
`download_file.__globals__['time']` so tests can drive retries without waiting.
Tests:
- attempt 1 raises, attempt 2 succeeds → returns `True`, partial file removed on
  the failed attempt, logged "after N retries".
- all attempts fail → returns `False` and no file remains.

---

## FP-4 — `save_text_file` double-extension sidecar  (S)

**Where:** `rss-podcast-downloader.py` `save_text_file` writes
`f'{filename}.txt'`, but callers pass `full_path` already ending in `.mp3`, so
sidecars come out as `episode.mp3.txt`.

**Problem:** Cosmetic but wrong (pre-existing, untouched by PR #2). Users get
`name.mp3.txt` instead of `name.txt`.

**Fix:** Strip the audio extension before appending `.txt` (or pass the
extension-free basename from the call site). Update
`tests/integration/test_download.py` `--save_text` coverage to assert the `.txt`
basename.

---

## FP-5 — Pin dev tooling + runtime `mutagen` for CI reproducibility  (S)

**Where:** `.github/workflows/ci.yml` (`uv pip install pytest ruff`), and
`requirements.txt` (`mutagen` is bare while every other dep is pinned).

**Problem:** Unpinned `pytest`, `ruff`, and `mutagen` let lint/format/test
behavior drift across CI runs months from now (ruff is strict + `format --check`
is enforced). Also note `ruff target-version = "py39"` in `pyproject.toml` while
local/CI run 3.14 — harmless today but a latent drift trap.

**Fix:** Pin `ruff`, `pytest`, and `mutagen` (match what's actually used, e.g.
ruff 0.16.x, pytest 9.x, mutagen 1.48.x), or add a `constraints.txt`/dev-extra.
Optionally bump `target-version` to `"py310"` or drop it.

---

## FP-6 — Single test coverage per commit (optional, CI hygiene)  (S)

**Where:** `.github/workflows/ci.yml` — `lint-and-unit` triggers on both `push`
(branches `**`) *and* `pull_request`, while `integration` is gated to
`pull_request`.

**Problem:** During PRs, unit tests + lint run twice per commit (push event + PR
event). Not a bug, just wasted CI.

**Fix (optional):** Gate `lint-and-unit` to `pull_request` as well, or drop the
`push` trigger, if single-per-commit coverage is preferred. Keep unit on push per
the original spec if fast push feedback is desired.

---

## Out of Scope (still, unchanged)

- Packaging the project as a pip/uv module / console entry point (`[project]`).
- Type-hint annotations / mypy.
- Renaming or moving the entry script.

---

## Execution

- Small independent items (FP-2, FP-4, FP-5, FP-6) are safe to batch in one PR.
- FP-1 and FP-3 touch `download_file` together and belong in the same PR.
- Suggested order: FP-1 → FP-3 → FP-2 → FP-4 → FP-5 → (FP-6).
- Each PR: `pytest -q` green + `ruff check .` + `ruff format --check .` clean.
- Per the repo's `sdd` convention, this doc lives in `docs/specs/` and is
  regenerable; keep it updated if scope drifts.
