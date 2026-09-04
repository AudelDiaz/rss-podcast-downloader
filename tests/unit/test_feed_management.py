"""Unit tests for the feed-management & incremental-sync features."""

import time


def _mk_pair(title, year):
    """Return an (entry, link) pair with a resolvable publish date."""
    entry = {
        'title': title,
        'published_parsed': time.struct_time((year, 1, 1, 0, 0, 0, 0, 0, -1)),
        'links': [],
    }
    return (entry, None)


def test_select_candidates_fresh_feed_num_episodes(mod):
    eps = [_mk_pair('e-2001', 2001), _mk_pair('e-2020', 2020), _mk_pair('e-2010', 2010)]
    sel = mod._select_candidates(eps, None, 1)
    assert [p[0]['title'] for p in sel] == ['e-2020']


def test_select_candidates_after_last_downloaded(mod):
    eps = [_mk_pair('e-2001', 2001), _mk_pair('e-2020', 2020), _mk_pair('e-2010', 2010)]
    last = mod._parse_date('2001-01-01')
    # Only episodes strictly after 2001 are candidates, newest first.
    sel = mod._select_candidates(eps, last, None)
    assert [p[0]['title'] for p in sel] == ['e-2020', 'e-2010']
    # num-episodes caps it.
    sel2 = mod._select_candidates(eps, last, 1)
    assert [p[0]['title'] for p in sel2] == ['e-2020']


def test_select_candidates_no_newer_episodes(mod):
    eps = [_mk_pair('e-2020', 2020)]
    last = mod._parse_date('2020-12-31')
    assert mod._select_candidates(eps, last, None) == []


def test_select_candidates_fewer_than_n(mod):
    eps = [_mk_pair('e-2020', 2020), _mk_pair('e-2019', 2019)]
    last = mod._parse_date('2018-01-01')
    sel = mod._select_candidates(eps, last, 10)
    assert [p[0]['title'] for p in sel] == ['e-2020', 'e-2019']


def test_select_candidates_rejects_nonpositive_num(mod):
    eps = [_mk_pair('e-2020', 2020)]
    assert mod._select_candidates(eps, None, 0) == []
    assert mod._select_candidates(eps, None, -1) == []


def test_get_last_downloaded_date_empty(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    feed_id = mod.get_or_create_feed(conn, 'http://a.com/feed', 'A')
    assert mod._get_last_downloaded_date(conn, feed_id) is None
    conn.close()


def test_get_last_downloaded_date_returns_max(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    feed_id = mod.get_or_create_feed(conn, 'http://a.com/feed', 'A')
    for title, pub in [('old', '2020-01-01T00:00:00'), ('new', '2025-06-15T10:00:00')]:
        conn.execute(
            'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (feed_id, title, title, pub, f'/x/{title}.mp3', 'now'),
        )
    conn.commit()
    last = mod._get_last_downloaded_date(conn, feed_id)
    assert last is not None and last.year == 2025 and last.month == 6
    conn.close()


def test_get_feed_url_by_id(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    feed_id = mod.get_or_create_feed(conn, 'http://a.com/feed', 'A')
    assert mod.get_feed_url_by_id(conn, feed_id) == 'http://a.com/feed'
    assert mod.get_feed_url_by_id(conn, 999) is None
    conn.close()


def test_prune_to_keep_last_keeps_newest(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    feed_id = mod.get_or_create_feed(conn, 'http://a.com/feed', 'A')
    rows = []
    dates = [
        ('old', '2020-01-01T00:00:00'),
        ('mid', '2023-01-01T00:00:00'),
        ('new', '2025-06-15T10:00:00'),
    ]
    for i, (title, pub) in enumerate(dates):
        path = save_dir / f'{title}.mp3'
        path.write_bytes(b'x')
        rows.append((feed_id, f'g{i}', title, pub, str(path), 'now'))
    conn.executemany(
        'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        rows,
    )
    conn.commit()

    mod.prune_to_keep_last(conn, feed_id, str(save_dir))

    remaining = conn.execute('SELECT title FROM episodes WHERE feed_id = ?', (feed_id,)).fetchall()
    assert [r[0] for r in remaining] == ['new']
    # Only the newest file survives on disk.
    files = sorted(p.name for p in save_dir.iterdir())
    assert files == ['new.mp3']
    conn.close()


def test_list_feeds_empty_db(capsys, mod, tmp_path):
    db = str(tmp_path / 'empty.db')
    conn = mod.setup_database(db)
    conn.close()
    mod.list_feeds(db)
    assert 'No feeds found in database.' in capsys.readouterr().out
