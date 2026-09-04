"""Tests for Sync UX improvements: audio/*, dry-run, anti-collision, verbose."""

from pathlib import Path


class _FakeLink:
    def __init__(self, href, ftype=''):
        self.href = href
        self.type = ftype


def test_is_audio_enclosure_accepts_variants(mod):
    assert mod._is_audio_enclosure(_FakeLink('http://x/ep.mp3', 'audio/mpeg')) is True
    assert mod._is_audio_enclosure(_FakeLink('http://x/ep.mp3', 'audio/mp3')) is True
    assert mod._is_audio_enclosure(_FakeLink('http://x/ep.ogg', 'audio/ogg')) is True
    assert mod._is_audio_enclosure(_FakeLink('http://x/ep.m4a', 'audio/mp4')) is True
    assert mod._is_audio_enclosure(_FakeLink('http://x/ep.mp4', 'video/mp4')) is True
    # Fallback by extension even when type missing
    assert mod._is_audio_enclosure(_FakeLink('http://x/ep.mp3', '')) is True
    assert mod._is_audio_enclosure(_FakeLink('http://x/ep.MP3', '')) is True
    # Non-audio should be rejected
    assert mod._is_audio_enclosure(_FakeLink('http://x/page.html', 'text/html')) is False
    assert mod._is_audio_enclosure(_FakeLink('http://x/image.jpg', 'image/jpeg')) is False


def test_unique_filepath_adds_suffix(mod, tmp_path):
    save_dir = tmp_path / 'podcasts'
    save_dir.mkdir()
    # Create first file
    first = save_dir / '2022-10-02_ep.mp3'
    first.write_bytes(b'x')
    # Should get _2
    result = mod._unique_filepath(str(save_dir), '2022-10-02_ep', '.mp3')
    assert result == str(save_dir / '2022-10-02_ep_2.mp3')
    # Create _2 as well
    (save_dir / '2022-10-02_ep_2.mp3').write_bytes(b'x')
    result2 = mod._unique_filepath(str(save_dir), '2022-10-02_ep', '.mp3')
    assert result2 == str(save_dir / '2022-10-02_ep_3.mp3')


def test_dry_run_does_not_download(mod, tmp_path):
    import time

    import feedparser

    # Build a minimal feed object with one audio/ogg entry (no real HTTP)
    feed = feedparser.FeedParserDict(
        {
            'feed': {'title': 'Test'},
            'entries': [
                {
                    'title': 'Episode OGG',
                    'id': 'ogg-1',
                    'published_parsed': time.struct_time((2022, 10, 2, 0, 0, 0, 0, 0, -1)),
                    'links': [_FakeLink('http://x/ep.ogg', 'audio/ogg')],
                }
            ],
        }
    )
    save_dir = tmp_path / 'out'
    save_dir.mkdir()
    conn = mod.setup_database(str(tmp_path / 'test.db'))
    feed_id = mod.get_or_create_feed(conn, 'http://x/feed', 'Test', save_dir=str(save_dir))

    mod.parse_and_download(
        str(save_dir),
        False,
        None,
        conn=conn,
        feed_id=feed_id,
        feed=feed,
        full_history=True,
        dry_run=True,
    )
    # No file created
    assert list(save_dir.iterdir()) == []
    # No DB row inserted
    rows = conn.execute('SELECT guid FROM episodes WHERE feed_id = ?', (feed_id,)).fetchall()
    assert rows == []
    conn.close()


def test_parse_and_download_audio_ogg_via_is_audio(mod, tmp_path):
    """Ensure audio/ogg is now accepted (was rejected before fix)."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import feedparser

    mp3_bytes = (Path(__file__).parent.parent / 'data' / 'silent.mp3').read_bytes()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/ep.ogg':
                self.send_response(200)
                self.send_header('Content-Type', 'audio/ogg')
                self.send_header('Content-Length', str(len(mp3_bytes)))
                self.end_headers()
                self.wfile.write(mp3_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), H)
    base = f'http://127.0.0.1:{server.server_address[1]}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        feed = feedparser.FeedParserDict(
            {
                'feed': {'title': 'Test'},
                'entries': [
                    feedparser.FeedParserDict(
                        {
                            'title': 'OGG Episode',
                            'id': 'ogg-1',
                            'published_parsed': time.struct_time((2022, 10, 2, 0, 0, 0, 0, 0, -1)),
                            'links': [_FakeLink(f'{base}/ep.ogg', 'audio/ogg')],
                        }
                    )
                ],
            }
        )
        save_dir = tmp_path / 'out2'
        save_dir.mkdir()
        conn = mod.setup_database(str(tmp_path / 'test2.db'))
        fid = mod.get_or_create_feed(conn, f'{base}/feed', 'Test', save_dir=str(save_dir))
        mod.parse_and_download(
            str(save_dir),
            False,
            None,
            conn=conn,
            feed_id=fid,
            feed=feed,
            full_history=True,
        )
        # File should exist with .ogg extension preserved from URL
        files = sorted(p.name for p in save_dir.iterdir())
        assert files == ['2022-10-02_ogg_episode.ogg']
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_dry_run_with_real_http_does_not_fetch(mod, tmp_path):
    """Dry-run on real HTTP fixture must not hit the download endpoint."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import feedparser

    mp3_bytes = (Path(__file__).parent.parent / 'data' / 'silent.mp3').read_bytes()
    FEED_XML = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
    <item><title>E1</title><guid>g1</guid><pubDate>Wed, 02 Oct 2002 13:00:00 GMT</pubDate>
    <enclosure url="{base}/ep.mp3" type="audio/mpeg" length="1000"/></item></channel></rss>"""

    class H(BaseHTTPRequestHandler):
        count = 0

        def do_GET(self):
            if self.path == '/feed.xml':
                body = FEED_XML.format(
                    base=f'http://127.0.0.1:{self.server.server_address[1]}'
                ).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == '/ep.mp3':
                H.count += 1
                self.send_response(200)
                self.send_header('Content-Length', str(len(mp3_bytes)))
                self.end_headers()
                self.wfile.write(mp3_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), H)
    base = f'http://127.0.0.1:{server.server_address[1]}'
    H.count = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        content = mod.fetch_rss_feed(f'{base}/feed.xml')
        feed = feedparser.parse(content)
        save_dir = tmp_path / 'out3'
        save_dir.mkdir()
        conn = mod.setup_database(str(tmp_path / 'test3.db'))
        fid = mod.get_or_create_feed(conn, f'{base}/feed.xml', 'T', save_dir=str(save_dir))
        mod.parse_and_download(
            str(save_dir),
            False,
            None,
            conn=conn,
            feed_id=fid,
            feed=feed,
            full_history=True,
            dry_run=True,
        )
        assert H.count == 0
        assert list(save_dir.iterdir()) == []
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
