"""Tests for rss-hardening-followups FPs.

Covers:
  FP-1 Content-Length malformed header guard
  FP-2 Legacy-schema migration branch
  FP-3 Retry/backoff with injectable sleep
  FP-4 save_text_file double-extension fix
"""

import sqlite3


def test_download_file_malformed_content_length_treated_as_no_length(mod, tmp_path):
    """FP-1: malformed Content-Length must not raise; body should still succeed."""

    class MalformedResponse:
        headers = {'Content-Length': 'abc'}

        class ctx:
            def __enter__(self):
                return MalformedResponse()

            def __exit__(self, *a):
                return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b'hello world'

    class FakeSession:
        @staticmethod
        def get(*args, **kwargs):
            return MalformedResponse.ctx()

    target = tmp_path / 'out.bin'
    result = mod.download_file('http://x/f', str(target), session=FakeSession(), retries=1)
    assert result is True
    assert target.read_bytes() == b'hello world'


def test_download_file_empty_content_length_treated_as_no_length(mod, tmp_path):
    """FP-1: empty Content-Length must not raise."""

    class EmptyResponse:
        headers = {'Content-Length': ''}

        class ctx:
            def __enter__(self):
                return EmptyResponse()

            def __exit__(self, *a):
                return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b'data'

    class FakeSession:
        @staticmethod
        def get(*args, **kwargs):
            return EmptyResponse.ctx()

    target = tmp_path / 'out2.bin'
    result = mod.download_file('http://x/f', str(target), session=FakeSession(), retries=1)
    assert result is True
    assert target.read_bytes() == b'data'


def test_legacy_migration_renames_old_episodes_table(mod, tmp_path):
    """FP-2: legacy episodes table lacking feed_id is renamed, new schema created."""
    db = str(tmp_path / 'legacy.db')
    c = sqlite3.connect(db)
    # Legacy schema: episodes without feed_id (old app version)
    c.execute(
        'CREATE TABLE episodes (episode_id INTEGER PRIMARY KEY, '
        'guid TEXT, title TEXT, published TEXT, filepath TEXT, downloaded_at TEXT)'
    )
    c.execute(
        'INSERT INTO episodes (guid, title, published, filepath, downloaded_at) '
        "VALUES ('g1','Old','2020-01-01','/x/old.mp3','now')"
    )
    c.commit()
    c.close()

    conn = mod.setup_database(db)
    # Old table renamed
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'episodes_old_pre_multi_feed' in tables
    assert 'episodes' in tables
    # New episodes has feed_id
    cols = [r[1] for r in conn.execute('PRAGMA table_info(episodes)')]
    assert 'feed_id' in cols
    # Legacy data preserved in renamed table
    legacy_rows = conn.execute('SELECT guid FROM episodes_old_pre_multi_feed').fetchall()
    assert legacy_rows == [('g1',)]
    conn.close()

    # Idempotent on second call: no second rename, no error
    conn2 = mod.setup_database(db)
    tables2 = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'episodes_old_pre_multi_feed' in tables2
    assert 'episodes' in tables2
    conn2.close()


def test_download_file_retries_then_succeeds(mod, tmp_path):
    """FP-3: first attempt fails, second succeeds — returns True and records sleep."""

    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)

    call_count = {'n': 0}

    class SuccessResponse:
        headers = {}

        class ctx:
            def __enter__(self):
                return SuccessResponse()

            def __exit__(self, *a):
                return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b'ok'

    class FakeSession:
        @staticmethod
        def get(*args, **kwargs):
            call_count['n'] += 1
            if call_count['n'] == 1:
                raise mod.requests.RequestException('boom')
            return SuccessResponse.ctx()

    target = tmp_path / 'retry.bin'
    result = mod.download_file(
        'http://x/f', str(target), session=FakeSession(), retries=3, sleep_fn=fake_sleep
    )
    assert result is True
    assert target.read_bytes() == b'ok'
    assert len(sleeps) == 1
    assert sleeps[0] == 2  # 2**1


def test_download_file_all_retries_fail(mod, tmp_path):
    """FP-3: all attempts fail — returns False and no file remains."""

    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)

    class FakeSession:
        @staticmethod
        def get(*args, **kwargs):
            raise mod.requests.RequestException('always fail')

    target = tmp_path / 'fail.bin'
    result = mod.download_file(
        'http://x/f', str(target), session=FakeSession(), retries=3, sleep_fn=fake_sleep
    )
    assert result is False
    assert not target.exists()
    assert len(sleeps) == 2  # retries=3 => 2 sleeps (after attempt 1 and 2)


def test_save_text_file_strips_mp3_extension(mod, tmp_path):
    """FP-4: sidecar must be ep.txt not ep.mp3.txt."""
    entry = {
        'title': 'Hello',
        'subtitle': 'Sub',
        'published': 'Wed, 02 Oct 2002 13:00:00 GMT',
        'summary': 'Some content',
    }
    audio_path = str(tmp_path / '2022-10-02_hello.mp3')
    # Create dummy audio file so path exists (not required but realistic)
    (tmp_path / '2022-10-02_hello.mp3').write_bytes(b'x')
    mod.save_text_file(entry, audio_path)
    assert (tmp_path / '2022-10-02_hello.txt').exists()
    assert not (tmp_path / '2022-10-02_hello.mp3.txt').exists()
    content = (tmp_path / '2022-10-02_hello.txt').read_text(encoding='utf-8')
    assert 'Title: Hello' in content
    assert 'Content: Some content' in content


def test_save_text_file_no_extension(mod, tmp_path):
    """FP-4: when filename has no extension, just append .txt."""
    entry = {'title': 'T', 'summary': 'S'}
    base = str(tmp_path / 'episode_no_ext')
    mod.save_text_file(entry, base)
    assert (tmp_path / 'episode_no_ext.txt').exists()
