"""Integration tests: end-to-end download against a local HTTP fixture server."""

import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mutagen.mp3 import MP3

# A real, tiny silent MP3 fixture so mutagen can open files for tagging.
TINY_MP3 = Path(__file__).resolve().parent.parent / 'data' / 'silent.mp3'
TINY_MP3_BYTES = TINY_MP3.read_bytes()

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Show</title>
    <author>Test Author</author>
    <item>
      <title>Newest Episode</title>
      <guid>newest-1</guid>
      <pubDate>Wed, 02 Oct 2002 13:00:00 GMT</pubDate>
      <enclosure url="{base}/ep-newest.mp3" type="audio/mpeg" length="1000"/>
    </item>
    <item>
      <title>Older Episode</title>
      <guid>older-1</guid>
      <pubDate>Wed, 01 Oct 2002 13:00:00 GMT</pubDate>
      <enclosure url="{base}/ep-older.mp3" type="audio/mpeg" length="1000"/>
    </item>
  </channel>
</rss>
"""


class _FeedHandler(BaseHTTPRequestHandler):
    feed_xml = ''
    download_count = {'ep-newest.mp3': 0, 'ep-older.mp3': 0}

    def do_GET(self):  # noqa: N802
        if self.path == '/feed.xml':
            body = self.feed_xml.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/rss+xml')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ('/ep-newest.mp3', '/ep-older.mp3'):
            name = self.path.lstrip('/')
            self.download_count[name] += 1
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.send_header('Content-Length', str(len(TINY_MP3_BYTES)))
            self.end_headers()
            self.wfile.write(TINY_MP3_BYTES)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture
def http_server(mod):
    server = ThreadingHTTPServer(('127.0.0.1', 0), _FeedHandler)
    base = f'http://127.0.0.1:{server.server_address[1]}'
    _FeedHandler.feed_xml = FEED_XML.format(base=base)
    _FeedHandler.download_count = {'ep-newest.mp3': 0, 'ep-older.mp3': 0}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()


def _feed(mod, http_server):
    import feedparser

    content = mod.fetch_rss_feed(f'{http_server}/feed.xml')
    return feedparser.parse(content)


def test_end_to_end_downloads_and_dedups(mod, http_server, tmp_path):
    save_dir = tmp_path / 'podcasts'
    save_dir.mkdir()
    db_path = tmp_path / 'test.db'

    feed = _feed(mod, http_server)
    conn = mod.setup_database(str(db_path))
    feed_id = mod.get_or_create_feed(conn, f'{http_server}/feed.xml', feed.feed.get('title', 'N/A'))

    # First pass: both episodes downloaded (fresh feed -> full_history).
    mod.parse_and_download(
        str(save_dir),
        False,
        None,
        conn=conn,
        feed_id=feed_id,
        feed=feed,
        full_history=True,
    )
    assert _FeedHandler.download_count == {'ep-newest.mp3': 1, 'ep-older.mp3': 1}
    files = sorted(p.name for p in save_dir.iterdir())
    assert files == ['2002-10-01_older_episode.mp3', '2002-10-02_newest_episode.mp3']

    # MP3 tags were written.
    tagged = MP3(save_dir / '2002-10-02_newest_episode.mp3')
    assert tagged.tags['TIT2'].text[0] == 'Newest Episode'
    assert tagged.tags['TALB'].text[0] == 'Test Show'

    # Second pass: nothing re-downloaded (dedup via DB).
    mod.parse_and_download(
        str(save_dir),
        False,
        None,
        conn=conn,
        feed_id=feed_id,
        feed=feed,
    )
    assert _FeedHandler.download_count == {'ep-newest.mp3': 1, 'ep-older.mp3': 1}

    conn.close()


def test_num_episodes_limits_to_newest(mod, http_server, tmp_path):
    save_dir = tmp_path / 'podcasts'
    save_dir.mkdir()
    db_path = tmp_path / 'test.db'

    feed = _feed(mod, http_server)
    conn = mod.setup_database(str(db_path))
    feed_id = mod.get_or_create_feed(conn, f'{http_server}/feed.xml', feed.feed.get('title', 'N/A'))

    # Only the newest episode should download with --num-episodes 1.
    mod.parse_and_download(
        str(save_dir),
        False,
        1,
        conn=conn,
        feed_id=feed_id,
        feed=feed,
    )
    assert _FeedHandler.download_count == {'ep-newest.mp3': 1, 'ep-older.mp3': 0}
    assert sorted(p.name for p in save_dir.iterdir()) == ['2002-10-02_newest_episode.mp3']

    conn.close()


def _seed_episode(conn, feed_id, guid, title, published, filepath):
    conn.execute(
        'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (feed_id, guid, title, published, filepath, 'now'),
    )
    conn.commit()


def test_incremental_sync_downloads_only_newer(mod, http_server, tmp_path):
    """With no --num-episodes, only episodes newer than the newest downloaded go."""
    save_dir = tmp_path / 'podcasts'
    save_dir.mkdir()
    db_path = tmp_path / 'test.db'

    feed = _feed(mod, http_server)
    conn = mod.setup_database(str(db_path))
    feed_id = mod.get_or_create_feed(conn, f'{http_server}/feed.xml', feed.feed.get('title', 'N/A'))

    # Simulate that the OLDER episode was already downloaded (2002-10-01).
    _seed_episode(conn, feed_id, 'older-1', 'Older Episode', '2002-10-01T13:00:00', '/x/older.mp3')

    mod.parse_and_download(str(save_dir), False, None, conn=conn, feed_id=feed_id, feed=feed)

    # Only the newer episode (published 2002-10-02) should be fetched.
    assert _FeedHandler.download_count == {'ep-newest.mp3': 1, 'ep-older.mp3': 0}
    assert sorted(p.name for p in save_dir.iterdir()) == ['2002-10-02_newest_episode.mp3']
    conn.close()


def test_incremental_sync_fully_caught_up_downloads_nothing(mod, http_server, tmp_path):
    """When the newest episode is already downloaded, no new downloads happen."""
    save_dir = tmp_path / 'podcasts'
    save_dir.mkdir()
    db_path = tmp_path / 'test.db'

    feed = _feed(mod, http_server)
    conn = mod.setup_database(str(db_path))
    feed_id = mod.get_or_create_feed(conn, f'{http_server}/feed.xml', feed.feed.get('title', 'N/A'))

    # Newest episode (2002-10-02) already present -> fully caught up.
    _seed_episode(
        conn,
        feed_id,
        'newest-1',
        'Newest Episode',
        '2002-10-02T13:00:00',
        '/x/newest.mp3',
    )

    mod.parse_and_download(str(save_dir), False, None, conn=conn, feed_id=feed_id, feed=feed)

    assert _FeedHandler.download_count == {'ep-newest.mp3': 0, 'ep-older.mp3': 0}
    assert sorted(p.name for p in save_dir.iterdir()) == []
    conn.close()


def test_new_feed_no_controls_downloads_nothing(mod, http_server, tmp_path):
    """A fresh feed with no --num-episodes/--since/--all must download nothing."""
    save_dir = tmp_path / 'podcasts'
    save_dir.mkdir()
    db_path = tmp_path / 'test.db'

    feed = _feed(mod, http_server)
    conn = mod.setup_database(str(db_path))
    feed_id = mod.get_or_create_feed(conn, f'{http_server}/feed.xml', feed.feed.get('title', 'N/A'))

    mod.parse_and_download(str(save_dir), False, None, conn=conn, feed_id=feed_id, feed=feed)

    assert _FeedHandler.download_count == {'ep-newest.mp3': 0, 'ep-older.mp3': 0}
    assert sorted(p.name for p in save_dir.iterdir()) == []
    conn.close()


def test_new_feed_since_seeds_date_window(mod, http_server, tmp_path):
    """--since on a fresh feed downloads only episodes from that date on."""
    save_dir = tmp_path / 'podcasts'
    save_dir.mkdir()
    db_path = tmp_path / 'test.db'

    feed = _feed(mod, http_server)
    conn = mod.setup_database(str(db_path))
    feed_id = mod.get_or_create_feed(conn, f'{http_server}/feed.xml', feed.feed.get('title', 'N/A'))

    # newest publishes 2002-10-02, older 2002-10-01. since 2002-10-02 -> only newest.
    since = datetime(2002, 10, 2)
    mod.parse_and_download(
        str(save_dir), False, None, conn=conn, feed_id=feed_id, feed=feed, since=since
    )

    assert _FeedHandler.download_count == {'ep-newest.mp3': 1, 'ep-older.mp3': 0}
    assert sorted(p.name for p in save_dir.iterdir()) == ['2002-10-02_newest_episode.mp3']
    conn.close()


def test_dateless_established_feed_not_blocked_by_guard(mod, http_server, tmp_path):
    """A feed that owns only dateless episodes must NOT be treated as brand-new.

    Regression: the guard keys off whether the feed has ANY rows, not off a dated
    anchor, so an all-dateless feed can still do incremental catch-up on a bare run.
    """
    save_dir = tmp_path / 'podcasts'
    save_dir.mkdir()
    db_path = tmp_path / 'test.db'

    feed = _feed(mod, http_server)
    conn = mod.setup_database(str(db_path))
    feed_id = mod.get_or_create_feed(conn, f'{http_server}/feed.xml', feed.feed.get('title', 'N/A'))

    # Seed the feed with one dateless (empty published) episode row, distinct guid.
    conn.execute(
        'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
        "VALUES (?, ?, ?, '', ?, ?)",
        (feed_id, 'already-done-dateless', 'old dateless', '/x/old.mp3', 'now'),
    )
    conn.commit()

    # A bare run must proceed (feed is established via rows) and download the
    # real dated episodes (2 of them), not be blocked by the new-feed guard.
    mod.parse_and_download(str(save_dir), False, None, conn=conn, feed_id=feed_id, feed=feed)

    assert _FeedHandler.download_count == {'ep-newest.mp3': 1, 'ep-older.mp3': 1}
    assert sorted(p.name for p in save_dir.iterdir()) == [
        '2002-10-01_older_episode.mp3',
        '2002-10-02_newest_episode.mp3',
    ]
    conn.close()
