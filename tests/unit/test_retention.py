"""Unit tests for flexible retention: --keep, --max-age, --max-size."""

from datetime import datetime, timedelta


def _seed(conn, feed_id, rows):
    """Insert episode rows; rows = list of (guid, title, published, filepath)."""
    for guid, title, pub, fp in rows:
        conn.execute(
            'INSERT INTO episodes (feed_id, guid, title, published, filepath, downloaded_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (feed_id, guid, title, pub, fp, 'now'),
        )
    conn.commit()


def test_parse_size_variants(mod):
    assert mod.parse_size('500') == 500
    assert mod.parse_size('1K') == 1024
    assert mod.parse_size('1KB') == 1024
    assert mod.parse_size('2M') == 2 * 1024**2
    assert mod.parse_size('1.5G') == int(1.5 * 1024**3)
    assert mod.parse_size('  500M  ') == 500 * 1024**2


def test_parse_max_age_variants(mod):
    assert mod.parse_max_age('30') == timedelta(days=30)
    assert mod.parse_max_age('30d') == timedelta(days=30)
    assert mod.parse_max_age('7days') == timedelta(days=7)
    assert mod.parse_max_age('1day') == timedelta(days=1)


def test_prune_keep_n_keeps_newest(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A')
    # 5 episodes newest = 2024
    rows = []
    for i, year in enumerate([2020, 2021, 2022, 2023, 2024]):
        p = save_dir / f'ep{i}.mp3'
        p.write_bytes(b'x' * 100)
        rows.append((f'g{i}', f'Ep {year}', f'{year}-01-01T00:00:00', str(p)))
    _seed(conn, fid, rows)

    kept, removed = mod.prune_feed(conn, fid, str(save_dir), keep=2)
    assert kept == 2
    assert removed == 3
    remaining = conn.execute('SELECT title FROM episodes ORDER BY published DESC').fetchall()
    assert [r[0] for r in remaining] == ['Ep 2024', 'Ep 2023']
    # Old files removed
    assert not (save_dir / 'ep0.mp3').exists()
    assert (save_dir / 'ep4.mp3').exists()
    conn.close()


def test_prune_max_age_keeps_new_and_dateless(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A')
    cutoff = datetime(2025, 6, 1)
    rows = [
        ('g-old', 'Old', '2025-01-01T00:00:00', str(save_dir / 'old.mp3')),
        ('g-new', 'New', '2025-06-10T00:00:00', str(save_dir / 'new.mp3')),
        ('g-dateless', 'Dateless', '', str(save_dir / 'dateless.mp3')),
    ]
    for _, _, _, fp in rows:
        if fp:
            import pathlib

            pathlib.Path(fp).write_bytes(b'x')
    _seed(conn, fid, rows)

    kept, removed = mod.prune_feed(conn, fid, str(save_dir), max_age=cutoff)
    assert removed == 1
    titles = {r[0] for r in conn.execute('SELECT title FROM episodes').fetchall()}
    assert 'Old' not in titles
    assert titles == {'New', 'Dateless'}
    assert not (save_dir / 'old.mp3').exists()
    assert (save_dir / 'new.mp3').exists()
    conn.close()


def test_prune_max_size_keeps_newest_until_cap(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A')
    # 3 files 600B each
    for i in range(3):
        p = save_dir / f'ep{i}.mp3'
        p.write_bytes(b'x' * 600)
    rows = [
        ('g0', 'Old', '2020-01-01T00:00:00', str(save_dir / 'ep0.mp3')),
        ('g1', 'Mid', '2023-01-01T00:00:00', str(save_dir / 'ep1.mp3')),
        ('g2', 'New', '2025-06-15T00:00:00', str(save_dir / 'ep2.mp3')),
    ]
    _seed(conn, fid, rows)

    # Cap 1KB -> only newest 600B fits, next would exceed
    kept, removed = mod.prune_feed(conn, fid, str(save_dir), max_size=1024)
    assert kept == 1
    assert removed == 2
    remaining = [r[0] for r in conn.execute('SELECT title FROM episodes').fetchall()]
    assert remaining == ['New']
    assert (save_dir / 'ep2.mp3').exists()
    assert not (save_dir / 'ep0.mp3').exists()
    conn.close()


def test_prune_composition_keep_and_max_age(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A')
    # keep 3, then max_age cuts oldest of those
    for i, _year in enumerate([2020, 2021, 2024, 2025]):
        p = save_dir / f'ep{i}.mp3'
        p.write_bytes(b'x')
    rows = [
        ('g0', 'Ep2020', '2020-01-01T00:00:00', str(save_dir / 'ep0.mp3')),
        ('g1', 'Ep2021', '2021-01-01T00:00:00', str(save_dir / 'ep1.mp3')),
        ('g2', 'Ep2024', '2024-01-01T00:00:00', str(save_dir / 'ep2.mp3')),
        ('g3', 'Ep2025', '2025-01-01T00:00:00', str(save_dir / 'ep3.mp3')),
    ]
    _seed(conn, fid, rows)
    # Keep 3 newest -> 2025,2024,2021; max_age cutoff 2023-01-01 removes 2021
    cutoff = datetime(2023, 1, 1)
    kept, removed = mod.prune_feed(conn, fid, str(save_dir), keep=3, max_age=cutoff)
    assert kept == 2
    titles = {r[0] for r in conn.execute('SELECT title FROM episodes').fetchall()}
    assert titles == {'Ep2025', 'Ep2024'}
    conn.close()


def test_prune_dry_run_does_not_delete(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A')
    p = save_dir / 'old.mp3'
    p.write_bytes(b'x')
    p2 = save_dir / 'new.mp3'
    p2.write_bytes(b'x')
    _seed(
        conn,
        fid,
        [
            ('g-old', 'Old', '2020-01-01T00:00:00', str(p)),
            ('g-new', 'New', '2025-01-01T00:00:00', str(p2)),
        ],
    )

    kept, removed = mod.prune_feed(conn, fid, str(save_dir), keep=1, dry_run=True)
    assert kept == 1 and removed == 1
    # Nothing deleted
    assert p.exists()
    assert conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 2
    conn.close()


def test_prune_preserves_shared_file(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A')
    shared = save_dir / 'shared.mp3'
    shared.write_bytes(b'x')
    other = save_dir / 'other.mp3'
    other.write_bytes(b'x')
    rows = [
        ('g-new', 'New', '2025-01-01T00:00:00', str(shared)),
        ('g-old-shared', 'OldShared', '2020-01-01T00:00:00', str(shared)),
        ('g-mid', 'Mid', '2023-01-01T00:00:00', str(other)),
    ]
    _seed(conn, fid, rows)

    kept, removed = mod.prune_feed(conn, fid, str(save_dir), keep=1)
    assert kept == 1
    assert shared.exists()
    assert not other.exists()
    conn.close()


def test_prune_to_keep_last_wrapper(mod, tmp_path):
    conn = mod.setup_database(str(tmp_path / 't.db'))
    save_dir = tmp_path / 'save'
    save_dir.mkdir()
    fid = mod.get_or_create_feed(conn, 'http://a/f', 'A')
    for i in range(3):
        p = save_dir / f'ep{i}.mp3'
        p.write_bytes(b'x')
        conn.execute(
            'INSERT INTO episodes (feed_id, guid, title, published, filepath, '
            'downloaded_at) VALUES (?, ?, ?, ?, ?, ?)',
            (fid, f'g{i}', f'Ep{i}', f'202{i}-01-01T00:00:00', str(p), 'now'),
        )
    conn.commit()
    mod.prune_to_keep_last(conn, fid, str(save_dir))
    assert conn.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 1
    conn.close()


def test_parse_size_invalid_raises(mod):
    import pytest

    with pytest.raises(ValueError):
        mod.parse_size('abc')
    with pytest.raises(ValueError):
        mod.parse_size('')


def test_parse_max_age_invalid(mod):
    import pytest

    with pytest.raises(ValueError):
        mod.parse_max_age('abc')
    with pytest.raises(ValueError):
        mod.parse_max_age('0d')
