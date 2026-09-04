"""Unit tests for database setup, feed lookup, and episode-selection logic."""


def test_setup_database_creates_schema(mod, tmp_path):
    db_path = tmp_path / 'test.db'
    conn = mod.setup_database(str(db_path))
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'feeds', 'episodes'} <= tables
    conn.close()


def test_setup_database_fresh_db_does_not_archive(mod, tmp_path, caplog):
    """A brand-new DB must not trigger the legacy-schema migration path."""
    import logging

    with caplog.at_level(logging.WARNING):
        conn = mod.setup_database(str(tmp_path / 'fresh.db'))
    assert 'Old database schema detected' not in caplog.text
    # episodes table exists with the new schema.
    cols = [row[1] for row in conn.execute('PRAGMA table_info(episodes)')]
    assert 'feed_id' in cols
    conn.close()


def test_get_or_create_feed_idempotent(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 'test.db'))
    first = mod.get_or_create_feed(conn, 'http://a.com/feed', 'Show A')
    second = mod.get_or_create_feed(conn, 'http://a.com/feed', 'Show A')
    assert first == second
    assert conn.execute('SELECT COUNT(*) FROM feeds').fetchone()[0] == 1
    conn.close()


def test_get_or_create_feed_creates_distinct_feeds(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 'test.db'))
    a = mod.get_or_create_feed(conn, 'http://a.com/feed', 'Show A')
    b = mod.get_or_create_feed(conn, 'http://b.com/feed', 'Show B')
    assert a != b
    conn.close()


class _FakeLink:
    def __init__(self, href, ftype):
        self.href = href
        self.type = ftype


def _entry(title, parsed):
    e = {'title': title, 'links': [_FakeLink(f'http://x/{title}.mp3', 'audio/mpeg')]}
    e['published_parsed'] = parsed
    return e


def test_num_episodes_selects_newest_not_first(mod):
    """--num-episodes must honor publication date, not feed list order."""
    import time as _time

    def parsed(year):
        return _time.struct_time((year, 1, 1, 0, 0, 0, 0, 0, -1))

    # Entries deliberately NOT in publication order (oldest listed first),
    # which is exactly the bug class the fix guards against.
    entries = [
        _entry('ep-2001', parsed(2001)),
        _entry('ep-2020', parsed(2020)),
        _entry('ep-2010', parsed(2010)),
    ]

    # Sort key alone must rank newest first (key receives (entry, link) tuples).
    pairs = [(e, e['links'][0]) for e in entries]
    ordered = sorted(pairs, key=mod._episode_sort_key, reverse=True)
    assert [e['title'] for e, _ in ordered[:1]] == ['ep-2020']
    assert [e['title'] for e, _ in ordered[:2]] == ['ep-2020', 'ep-2010']


def test_episode_sort_key_newest_first(mod):
    import time as _time

    entries = [
        _entry('old', _time.struct_time((2001, 1, 1, 0, 0, 0, 0, 0, -1))),
        _entry('new', _time.struct_time((2020, 1, 1, 0, 0, 0, 0, 0, -1))),
    ]
    keyed = [(e, mod._episode_sort_key((e, None))) for e in entries]
    assert max(keyed, key=lambda t: t[1])[0]['title'] == 'new'
    assert min(keyed, key=lambda t: t[1])[0]['title'] == 'old'


def test_sort_key_uses_string_date_when_no_parsed(mod):
    """Ordering must fall back to the raw date string, matching filename logic.

    Guards the regression where a feedparser entry lacking ``*_parsed`` but with
    a parseable string date was ranked as dateless (oldest) for ordering while
    still getting a date prefix in its filename.
    """
    # One entry has *_parsed; another lacks it but its string IS parseable and newer.
    import time as _time

    with_parsed = _entry('has-parsed', _time.struct_time((2001, 1, 1, 0, 0, 0, 0, 0, -1)))
    string_only = {
        'title': 'string-only',
        'published': 'Wed, 02 Oct 2002 13:00:00 GMT',
        'links': [_FakeLink('http://x/string-only.mp3', 'audio/mpeg')],
    }
    pairs = [(with_parsed, None), (string_only, None)]
    ordered = sorted(pairs, key=mod._episode_sort_key, reverse=True)
    # 2002 string date is newer than the 2001 parsed date.
    assert ordered[0][0]['title'] == 'string-only'
    # And the filename prefix agrees with the ordering.
    assert mod.entry_date_prefix(string_only) == '2002-10-02'


def test_download_file_content_length_mismatch_returns_false(mod, tmp_path):
    """A body shorter than the declared Content-Length must count as a failure."""

    class ShortResponse:
        headers = {'Content-Length': '1000'}

        class ctx:
            def __enter__(self):
                return ShortResponse()

            def __exit__(self, *a):
                return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b'a' * 10  # far less than the declared 1000

    class FakeSession:
        @staticmethod
        def get(*args, **kwargs):
            return ShortResponse.ctx()

    target = tmp_path / 'out.bin'
    # A single attempt is enough: mismatch is a failure on the first try.
    result = mod.download_file('http://x/f', str(target), session=FakeSession(), retries=1)
    assert result is False


def test_download_file_oserror_returns_false(mod, tmp_path):
    """A write OSError (not a RequestException) must not raise; returns False."""

    class BoomResponse:
        headers = {}

        class ctx:
            def __enter__(self):
                return BoomResponse()

            def __exit__(self, *a):
                return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            raise OSError('disk full')

    class FakeSession:
        @staticmethod
        def get(*args, **kwargs):
            return BoomResponse.ctx()

    target = tmp_path / 'out.bin'
    result = mod.download_file('http://x/f', str(target), session=FakeSession(), retries=1)
    assert result is False
    assert not target.exists()  # partial file cleaned up
