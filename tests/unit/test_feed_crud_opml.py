"""Unit tests for feed CRUD + OPML."""


def test_remove_feed_no_delete_files_leaves_files(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A', save_dir=str(save_dir))
    p = save_dir / 'ep.mp3'
    p.write_bytes(b'x')
    conn.execute(
        'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (fid, 'g1', 'Ep', '2025-01-01T00:00:00', str(p), 'now'),
    )
    conn.commit()

    ok = mod.remove_feed(conn, fid, delete_files=False)
    assert ok is True
    assert p.exists()
    assert conn.execute('SELECT COUNT(*) FROM feeds').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 0
    conn.close()


def test_remove_feed_with_delete_files_removes_under_save_dir(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A', save_dir=str(save_dir))
    p = save_dir / 'ep.mp3'
    p.write_bytes(b'x')
    conn.execute(
        'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (fid, 'g1', 'Ep', '2025-01-01T00:00:00', str(p), 'now'),
    )
    conn.commit()

    ok = mod.remove_feed(conn, fid, delete_files=True)
    assert ok is True
    assert not p.exists()
    conn.close()


def test_remove_feed_with_delete_files_not_outside_save_dir(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    outside = tmp_path / 'outside.mp3'
    outside.write_bytes(b'x')
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A', save_dir=str(save_dir))
    conn.execute(
        'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (fid, 'g1', 'Ep', '2025-01-01T00:00:00', str(outside), 'now'),
    )
    conn.commit()

    mod.remove_feed(conn, fid, delete_files=True)
    # File outside save_dir must NOT be deleted
    assert outside.exists()
    conn.close()


def test_remove_feed_nonexistent_returns_false(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    assert mod.remove_feed(conn, 999, delete_files=False) is False
    conn.close()


def test_export_import_opml_roundtrip(mod, tmp_path):
    db = str(tmp_path / 't.db')
    conn = mod.setup_database(db)
    mod.get_or_create_feed(conn, 'http://a/f', 'Feed A')
    mod.get_or_create_feed(conn, 'http://b/f', 'Feed B')
    conn.close()

    opml = tmp_path / 'feeds.opml'
    count = mod.export_opml(db, str(opml))
    assert count == 2
    assert opml.exists()
    content = opml.read_text(encoding='utf-8')
    assert 'http://a/f' in content
    assert 'http://b/f' in content

    # Fresh DB, import
    db2 = str(tmp_path / 't2.db')
    imported, skipped = mod.import_opml(db2, str(opml))
    assert imported == 2
    assert skipped == 0
    # Import again -> skip
    imported2, skipped2 = mod.import_opml(db2, str(opml))
    assert imported2 == 0
    assert skipped2 == 2
    conn2 = mod.setup_database(db2)
    rows = conn2.execute('SELECT feed_url FROM feeds').fetchall()
    assert len(rows) == 2
    conn2.close()


def test_import_opml_no_feeds(mod, tmp_path):
    db = str(tmp_path / 't.db')
    opml = tmp_path / 'empty.opml'
    xml = (
        '<?xml version="1.0"?><opml version="2.0"><head><title>x</title></head><body></body></opml>'
    )
    opml.write_text(xml, encoding='utf-8')
    imported, skipped = mod.import_opml(db, str(opml))
    assert (imported, skipped) == (0, 0)
