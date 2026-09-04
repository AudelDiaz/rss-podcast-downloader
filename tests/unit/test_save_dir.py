"""Unit tests for the per-feed save_dir feature."""

import os
import sqlite3


def test_setup_database_fresh_db_has_save_dir_column(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 'fresh.db'))
    cols = [row[1] for row in conn.execute('PRAGMA table_info(feeds)')]
    assert 'save_dir' in cols
    conn.close()


def test_setup_database_adds_save_dir_to_existing_db(mod, tmp_path):
    """A pre-existing feeds table without save_dir gets the column added."""
    db = str(tmp_path / 'legacy.db')
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE feeds (feed_id INTEGER PRIMARY KEY, feed_url TEXT, feed_title TEXT)')
    c.execute("INSERT INTO feeds (feed_url, feed_title) VALUES ('http://a.com/f', 'A')")
    c.commit()
    c.close()

    conn = mod.setup_database(db)
    cols = [row[1] for row in conn.execute('PRAGMA table_info(feeds)')]
    assert 'save_dir' in cols
    conn.close()

    # Idempotent on a second call.
    conn2 = mod.setup_database(db)
    cols2 = [row[1] for row in conn2.execute('PRAGMA table_info(feeds)')]
    assert 'save_dir' in cols2
    conn2.close()


def test_backfill_save_dir_from_newest_episode(mod, tmp_path):
    """Feed with episodes but no save_dir recovers dir of the newest episode."""
    db = str(tmp_path / 'back.db')
    conn = mod.setup_database(db)
    feed_id = mod.get_or_create_feed(conn, 'http://a.com/f', 'A', save_dir=None)
    conn.execute('UPDATE feeds SET save_dir = NULL WHERE feed_id = ?', (feed_id,))
    conn.executemany(
        'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        [
            (feed_id, 'g1', 'old', '2020-01-01T00:00:00', '/old/dir/old.mp3', 'now'),
            (feed_id, 'g2', 'new', '2025-06-15T00:00:00', '/new/dir/new.mp3', 'now'),
        ],
    )
    conn.commit()
    conn.close()

    conn2 = mod.setup_database(db)
    saved = conn2.execute('SELECT save_dir FROM feeds WHERE feed_id = ?', (feed_id,)).fetchone()[0]
    assert saved == '/new/dir'  # newest episode_id (g2) dir wins
    conn2.close()


def test_set_feed_save_dir_normalizes_to_absolute(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    feed_id = mod.get_or_create_feed(conn, 'http://a.com/f', 'A')
    mod.set_feed_save_dir(conn, feed_id, 'relative/path')
    assert mod.get_feed_save_dir(conn, feed_id) == os.path.abspath('relative/path')
    conn.close()


def test_get_or_create_feed_stores_save_dir_on_insert(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    feed_id = mod.get_or_create_feed(conn, 'http://a.com/f', 'A', save_dir='/x/y')
    assert mod.get_feed_save_dir(conn, feed_id) == '/x/y'
    conn.close()


def test_get_or_create_feed_updates_save_dir_on_existing(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    feed_id = mod.get_or_create_feed(conn, 'http://a.com/f', 'A')
    assert mod.get_feed_save_dir(conn, feed_id) is None
    again = mod.get_or_create_feed(conn, 'http://a.com/f', 'A', save_dir='/new/dir')
    assert again == feed_id
    assert mod.get_feed_save_dir(conn, feed_id) == '/new/dir'
    conn.close()


def test_list_feeds_includes_save_dir(capsys, mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    conn.execute(
        "INSERT INTO feeds (feed_url, feed_title, save_dir) VALUES ('http://a/f', 'A', '/d')"
    )
    conn.commit()
    conn.close()
    mod.list_feeds(str(tmp_path / 't.db'))
    out = capsys.readouterr().out
    assert 'http://a/f | A | /d' in out
