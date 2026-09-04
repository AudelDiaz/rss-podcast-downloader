"""RSS Podcast Downloader.

Description: This script downloads audio files (and optionally accompanying text
information) from a specified RSS feed, writing clean, portable filenames and
enriching the media library with episode metadata (title, album, artist) on the
downloaded MP3 files.

A local SQLite database (downloads.db) tracks all downloaded episodes so reruns
skip already-fetched episodes and multiple feeds can be managed independently.

Usage:
    python rss-podcast-downloader.py <RSS_FEED_URL> <SAVE_DIRECTORY> [--save_text]
    python rss-podcast-downloader.py --feed-id N [SAVE_DIRECTORY] [--save_text]

Options:
    --feed-id N        Pull a feed already stored in the database by its id.
    --list-feeds       List all feeds in the database (with their save dirs) and exit.
    --remove-feed ID   Remove feed ID and its episodes (use --delete-files to also delete files).
    --export-opml FILE Export feeds to OPML file.
    --import-opml FILE Import feeds from OPML file.
    --keep-last        Keep only the newest episode per feed after downloading.
    --keep N           Keep only N newest episodes (flexible retention).
    --max-age DAYS     Keep only episodes newer than DAYS days (e.g. 30, 30d).
    --max-size SIZE    Keep newest until total size <= SIZE (e.g. 500M, 2G).
    --num-episodes N   Only consider N episodes published after the last download.
    --since DATE       Only consider episodes published on/after DATE (YYYY-MM-DD).
    --all              On a feed with no prior downloads, download the full archive.
    --save_text        Also save a .txt sidecar with episode details.
    --dry-run          Show what would be downloaded without downloading.
    --verbose          Enable DEBUG logging.
    --quiet            Suppress INFO logging (WARNING only).

A feed with no prior downloads requires one of --num-episodes, --since, or --all;
otherwise nothing is downloaded.

Example:
    python rss-podcast-downloader.py http://example.com/podcast.rss ./podcasts/MyShow

Note:
    Feeds that require an authentication token can embed it in the URL.

Please use responsibly and in accordance with the RSS provider's terms of
service. This script is intended for personal use.

Author: John Sosoka
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import feedparser
import requests
from mutagen.id3 import COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2
from mutagen.mp3 import MP3

__version__ = '1.1.0'

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

USER_AGENT = f'rss-podcast-downloader/{__version__} (+https://github.com/johnsosoka/rss-podcast-downloader)'


def find_config_path(explicit=None, no_config=False):
    """Resolve config file path by priority.

    Priority: explicit > ./rss-podcast-downloader.toml > XDG > ~/.config/...
    Returns Path or None.
    """
    if no_config:
        return None
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() and p.is_file() else p
    candidates = [
        Path.cwd() / 'rss-podcast-downloader.toml',
        Path(__file__).parent / 'rss-podcast-downloader.toml',
    ]
    xdg = os.environ.get('XDG_CONFIG_HOME')
    if xdg:
        candidates.append(Path(xdg) / 'rss-podcast-downloader' / 'config.toml')
    candidates.append(Path.home() / '.config' / 'rss-podcast-downloader' / 'config.toml')
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def load_config(path=None):
    """Load TOML config and return defaults dict.

    Supports [defaults] table or top-level scalar keys. Returns {} if missing.
    """
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import tomllib  # py311+
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            logging.warning('tomllib not available, cannot load config %s', p)
            return {}
    try:
        with open(p, 'rb') as f:
            data = tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        logging.warning('Failed to parse config %s: %s', p, e)
        return {}
    if 'defaults' in data and isinstance(data['defaults'], dict):
        return dict(data['defaults'])
    # Fallback: top-level scalar keys
    known = {
        'save_dir',
        'keep',
        'max_age',
        'max_size',
        'verbose',
        'quiet',
        'save_text',
        'num_episodes',
        'since',
        'all',
        'keep_last',
        'dry_run',
    }
    result = {}
    for k, v in data.items():
        nk = k.replace('-', '_')
        if nk in known:
            result[nk] = v
        elif isinstance(v, dict):
            continue
        elif k in known:
            result[k] = v
    return result


# Date formats commonly found in RSS <pubDate> elements.
DATE_FORMATS = [
    '%Y-%m-%d',  # already-formatted date-only prefix
    '%a, %d %b %Y %H:%M:%S %Z',  # e.g. "Wed, 02 Oct 2002 13:00:00 GMT"
    '%a, %d %b %Y %H:%M:%S %z',  # timezone offset, e.g. +0000
    '%a, %d %b %Y %H:%M:%S',  # no timezone
]


def _parse_date(date_str):
    """Parse a date string to a naive datetime, trying several common encodings.

    Returns ``None`` if the string cannot be parsed.
    """
    if not date_str:
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # ISO-8601 (handles a trailing 'Z' by normalizing to +00:00).
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except ValueError:
        pass

    # RFC-2822 / RFC-822 via the stdlib (broad fallback).
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _published_parsed_as_date(entry):
    """Return a naive datetime from a feedparser ``*_parsed`` field, if present."""
    for key in ('published_parsed', 'updated_parsed'):
        ts = entry.get(key)
        if ts:
            try:
                return datetime(*ts[:6])
            except (TypeError, ValueError):
                continue
    return None


def _entry_datetime(entry):
    """Resolve an entry's publication datetime using the same logic everywhere.

    Prefers feedparser's structured ``*_parsed`` field, then falls back to
    parsing the raw ``published``/``updated`` string. Used both for the filename
    date prefix and for recency ordering, so an entry never sorts as "dateless"
    while still receiving a date prefix in its filename.
    """
    dt = _published_parsed_as_date(entry)
    if dt is None:
        dt = _parse_date(entry.get('published') or entry.get('updated'))
    return dt


def entry_date_prefix(entry):
    """Return the ``YYYY-MM-DD`` publication-date prefix for an entry, or None."""
    dt = _entry_datetime(entry)
    return dt.strftime('%Y-%m-%d') if dt else None


def _sanitize_basename(title):
    """Convert a title to a clean, ASCII, filesystem-friendly basename."""
    # Normalize unicode characters to their ASCII equivalent.
    try:
        normalized = unicodedata.normalize('NFKD', title)
        ascii_title = normalized.encode('ascii', 'ignore').decode('ascii')
    except Exception as e:  # pragma: no cover - defensive
        logging.warning("Could not normalize title '%s'. Using it as is. Error: %s", title, e)
        ascii_title = title

    # Replace spaces with underscores, then strip anything unexpected.
    sanitized = ascii_title.replace(' ', '_')
    sanitized = re.sub(r'[^a-zA-Z0-9._-]', '', sanitized)

    # Collapse repeated separators and trim leading/trailing ones.
    sanitized = re.sub(r'__+', '_', sanitized)
    sanitized = re.sub(r'--+', '-', sanitized)
    sanitized = sanitized.strip('_-')

    # Empty/blank titles are not valid filenames.
    if not sanitized:
        sanitized = 'untitled'
    return sanitized.lower()


def sanitize_title(title, date_str=None):
    """Convert a title to a filesystem-friendly name with an optional date prefix.

    ``date_str`` may be a raw feed date (RFC-822, ISO-8601, etc.) or an already
    formatted ``YYYY-MM-DD`` string. Returns ``YYYY-MM-DD_ascii_title`` when a
    date is parseable, otherwise just the ASCII title.
    """
    sanitized = _sanitize_basename(title)

    if date_str:
        dt = _parse_date(date_str)
        if dt is not None:
            sanitized = f'{dt.strftime("%Y-%m-%d")}_{sanitized}'
        else:
            logging.warning(
                "Could not parse date: '%s'. Filename will not have a date prefix.", date_str
            )
    return sanitized


def sanitize_filename_from_entry(entry):
    """Build the ``YYYY-MM-DD_ascii_title`` filename (no extension) for an entry."""
    prefix = entry_date_prefix(entry)
    return sanitize_title(entry.get('title', 'untitled'), prefix)


def download_file(url, filename, session=None, retries=3, sleep_fn=None):
    """Stream a file from ``url`` to ``filename`` with retry/backoff logic.

    Returns ``True`` on success. A partial file is removed before each retry so
    a truncated download is never mistaken for a complete one.
    """
    session = session if session is not None else requests
    _sleep = sleep_fn if sleep_fn is not None else time.sleep
    headers = {'User-Agent': USER_AGENT}

    for attempt in range(1, retries + 1):
        try:
            with session.get(url, headers=headers, stream=True, timeout=60) as response:
                response.raise_for_status()
                # Where the server declares a length, verify we received all of it
                # so a clean-but-truncated body is still treated as a failure.
                expected = response.headers.get('Content-Length')
                if expected is not None:
                    try:
                        expected = int(expected)
                    except (TypeError, ValueError):
                        expected = None
                written = 0
                with open(filename, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            written += file.write(chunk)
                if expected is not None and written != expected:
                    raise requests.RequestException(
                        f'Expected {expected} bytes, received {written}.'
                    )
            if attempt > 1:
                logging.info('Download succeeded after %s retries: %s', attempt - 1, filename)
            else:
                logging.info('Downloaded: %s', filename)
            return True
        except (requests.RequestException, OSError) as e:
            logging.warning('Error downloading file (attempt %s/%s): %s', attempt, retries, e)
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                except OSError:
                    logging.warning('Could not remove partial file: %s', filename)
            if attempt < retries:
                sleep_time = 2**attempt  # Exponential backoff: 2, 4, 8 seconds.
                logging.info('Retrying in %s seconds...', sleep_time)
                _sleep(sleep_time)
            else:
                logging.error('Failed to download %s after %s attempts.', url, retries)
    return False


def fetch_rss_feed(url, session=None):
    """Fetch the raw content of an RSS feed, exiting the process on failure."""
    session = session if session is not None else requests
    try:
        response = session.get(url, headers={'User-Agent': USER_AGENT}, timeout=30)
        response.raise_for_status()
        return response.content
    except requests.RequestException as e:
        logging.error('Error fetching the RSS feed: %s', e)
        logging.error('Please check the URL and authentication token (if applicable)')
        logging.error('Exiting...')
        raise SystemExit(1) from None


def setup_database(db_path=None):
    """Initialize the SQLite database (defaults to ``downloads.db`` next to the script)."""
    if db_path is None:
        script_dir = os.path.dirname(os.path.realpath(__file__))
        db_path = os.path.join(script_dir, 'downloads.db')

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Simple migration: if a legacy "episodes" table exists without feed_id,
    # archive it so the new multi-feed schema can be created cleanly. A missing
    # episodes table (fresh DB) is a no-op, not a migration trigger.
    episodes_exists = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='episodes'"
    ).fetchone()
    if episodes_exists:
        cursor.execute('PRAGMA table_info(episodes)')
        columns = [row[1] for row in cursor.fetchall()]
        if 'feed_id' not in columns:
            logging.warning(
                'Old database schema detected. Archiving old "episodes" table and '
                'creating new schema. Download history will be reset.'
            )
            cursor.execute('ALTER TABLE episodes RENAME TO episodes_old_pre_multi_feed')

    # Create tables with the new schema.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS feeds (
            feed_id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_url TEXT UNIQUE NOT NULL,
            feed_title TEXT,
            save_dir TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS episodes (
            episode_id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_id INTEGER NOT NULL,
            guid TEXT NOT NULL,
            title TEXT,
            published TEXT,
            filepath TEXT,
            downloaded_at TEXT,
            FOREIGN KEY (feed_id) REFERENCES feeds (feed_id),
            UNIQUE (feed_id, guid)
        )
        """
    )

    # Add the save_dir column to existing databases that predate it.
    feeds_columns = [row[1] for row in cursor.execute('PRAGMA table_info(feeds)')]
    if 'save_dir' not in feeds_columns:
        logging.warning('Adding "save_dir" column to existing feeds table.')
        cursor.execute('ALTER TABLE feeds ADD COLUMN save_dir TEXT')

    _backfill_feeds_save_dir(cursor)
    conn.commit()
    logging.info('Database setup complete at %s', db_path)
    return conn


def _backfill_feeds_save_dir(cursor):
    """For feeds with episodes but no save_dir, recover it from the newest filepath.

    The destination folder is the directory of the most recently downloaded
    episode (highest episode_id) for that feed.
    """
    rows = cursor.execute(
        """
        SELECT e.feed_id, e.filepath
        FROM episodes e
        JOIN feeds f ON f.feed_id = e.feed_id
        WHERE (f.save_dir IS NULL OR f.save_dir = '')
          AND e.filepath IS NOT NULL AND e.filepath != ''
          AND e.episode_id = (
              SELECT MAX(e2.episode_id) FROM episodes e2 WHERE e2.feed_id = e.feed_id
          )
        """
    ).fetchall()
    for feed_id, filepath in rows:
        save_dir = os.path.dirname(os.path.abspath(filepath))
        if save_dir:
            cursor.execute('UPDATE feeds SET save_dir = ? WHERE feed_id = ?', (save_dir, feed_id))
            logging.info('Back-filled save_dir for feed %s: %s', feed_id, save_dir)


def get_or_create_feed(conn, feed_url, feed_title, save_dir=None):
    """Get the ``feed_id`` for a ``feed_url``, creating the feed if it doesn't exist."""
    cursor = conn.cursor()
    cursor.execute('SELECT feed_id FROM feeds WHERE feed_url = ?', (feed_url,))
    result = cursor.fetchone()
    # Normalize once so the INSERT and the UPDATE paths persist the same value.
    save_dir = os.path.abspath(save_dir) if save_dir else None
    if result:
        feed_id = result[0]
        if save_dir is not None:
            set_feed_save_dir(conn, feed_id, save_dir)
        return feed_id
    cursor.execute(
        'INSERT INTO feeds (feed_url, feed_title, save_dir) VALUES (?, ?, ?)',
        (feed_url, feed_title, save_dir),
    )
    conn.commit()
    logging.info('Added new feed to database: %s', feed_title)
    return cursor.lastrowid


def get_feed_save_dir(conn, feed_id):
    """Return the stored ``save_dir`` for ``feed_id``, or None if not set."""
    cursor = conn.cursor()
    cursor.execute('SELECT save_dir FROM feeds WHERE feed_id = ?', (feed_id,))
    result = cursor.fetchone()
    value = result[0] if result else None
    return value or None


def set_feed_save_dir(conn, feed_id, save_dir):
    """Persist the (normalized, absolute) save directory for a feed."""
    if not save_dir:
        return
    abs_dir = os.path.abspath(save_dir)
    cursor = conn.cursor()
    cursor.execute('UPDATE feeds SET save_dir = ? WHERE feed_id = ?', (abs_dir, feed_id))
    conn.commit()


def list_feeds(db_path=None):
    """Print every feed stored in the database, one per line.

    Uses ``downloads.db`` next to the script unless ``db_path`` is provided.
    Runs schema setup so pre-existing databases are migrated first.
    """
    conn = setup_database(db_path)
    try:
        rows = conn.execute('SELECT feed_id, feed_url, feed_title, save_dir FROM feeds').fetchall()
    finally:
        conn.close()

    if not rows:
        print('No feeds found in database.')
        return
    for feed_id, feed_url, feed_title, save_dir in rows:
        print(f'{feed_id} | {feed_url} | {feed_title} | {save_dir or ""}')


def get_feed_url_by_id(conn, feed_id):
    """Return the stored ``feed_url`` for ``feed_id``, or None if not found."""
    cursor = conn.cursor()
    cursor.execute('SELECT feed_url FROM feeds WHERE feed_id = ?', (feed_id,))
    result = cursor.fetchone()
    return result[0] if result else None


def remove_feed(conn, feed_id, delete_files=False):
    """Remove a feed and its episodes.

    Args:
        delete_files: if True, also delete on-disk files under the feed's save_dir.
    Returns True if feed existed and was removed, False otherwise.
    """
    cursor = conn.cursor()
    cursor.execute('SELECT save_dir FROM feeds WHERE feed_id = ?', (feed_id,))
    row = cursor.fetchone()
    if not row:
        return False
    save_dir = row[0]

    filepaths = []
    if delete_files and save_dir:
        filepaths = [
            r[0]
            for r in cursor.execute(
                'SELECT filepath FROM episodes WHERE feed_id = ?', (feed_id,)
            ).fetchall()
            if r[0]
        ]

    cursor.execute('DELETE FROM episodes WHERE feed_id = ?', (feed_id,))
    cursor.execute('DELETE FROM feeds WHERE feed_id = ?', (feed_id,))
    conn.commit()

    if delete_files and save_dir and filepaths:
        abs_save = os.path.abspath(save_dir)
        for fp in filepaths:
            try:
                abs_fp = os.path.abspath(fp)
                if os.path.commonpath([abs_save, abs_fp]) == abs_save and os.path.exists(fp):
                    os.remove(fp)
                    logging.info('Removed file for deleted feed %s: %s', feed_id, fp)
            except (OSError, ValueError):
                pass
    logging.info(
        'Removed feed %s (%s episode(s))', feed_id, len(filepaths) if delete_files else 'unknown'
    )
    return True


def export_opml(db_path=None, output_path=None):
    """Export all feeds to an OPML file."""
    import xml.etree.ElementTree as ET

    if output_path is None:
        raise ValueError('output_path required')
    conn = setup_database(db_path)
    try:
        rows = conn.execute('SELECT feed_url, feed_title FROM feeds ORDER BY feed_id').fetchall()
    finally:
        conn.close()

    opml = ET.Element('opml', version='2.0')
    head = ET.SubElement(opml, 'head')
    ET.SubElement(head, 'title').text = 'RSS Podcast Downloader Feeds'
    body = ET.SubElement(opml, 'body')
    for feed_url, feed_title in rows:
        ET.SubElement(
            body,
            'outline',
            type='rss',
            text=feed_title or feed_url,
            title=feed_title or feed_url,
            xmlUrl=feed_url,
        )

    tree = ET.ElementTree(opml)
    # Pretty? Minimal — write with xml declaration
    ET.indent(tree, space='  ') if hasattr(ET, 'indent') else None
    tree.write(output_path, encoding='utf-8', xml_declaration=True)
    logging.info('Exported %s feed(s) to %s', len(rows), output_path)
    return len(rows)


def import_opml(db_path=None, input_path=None):
    """Import feeds from an OPML file. Returns (imported, skipped)."""
    import xml.etree.ElementTree as ET

    if input_path is None or not os.path.exists(input_path):
        raise FileNotFoundError(f'OPML file not found: {input_path}')
    tree = ET.parse(input_path)
    root = tree.getroot()
    # Collect all outline elements with xmlUrl (recursively)
    urls = []
    for outline in root.iter('outline'):
        xml_url = outline.get('xmlUrl') or outline.get('xmlurl')
        if xml_url:
            title = outline.get('text') or outline.get('title') or xml_url
            urls.append((xml_url.strip(), title.strip()))

    if not urls:
        logging.warning('No feeds found in OPML: %s', input_path)
        return (0, 0)

    conn = setup_database(db_path)
    imported = 0
    skipped = 0
    try:
        for feed_url, feed_title in urls:
            cursor = conn.cursor()
            cursor.execute('SELECT feed_id FROM feeds WHERE feed_url = ?', (feed_url,))
            if cursor.fetchone():
                skipped += 1
                continue
            cursor.execute(
                'INSERT INTO feeds (feed_url, feed_title) VALUES (?, ?)',
                (feed_url, feed_title),
            )
            imported += 1
        conn.commit()
    finally:
        conn.close()
    logging.info('Imported %s feed(s), skipped %s existing', imported, skipped)
    return (imported, skipped)


def _get_last_downloaded_date(conn, feed_id):
    """Return the newest ``published`` datetime already downloaded for a feed.

    Returns None when no dated episodes have been downloaded for the feed yet.
    (An all-dateless feed has no dated anchor; use :func:`_feed_has_episodes`
    to distinguish that from a truly brand-new feed.)
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT MAX(published) FROM episodes WHERE feed_id = ? AND published != ''",
        (feed_id,),
    )
    value = cursor.fetchone()[0]
    if not value:
        return None
    # published is stored as 'YYYY-MM-DDTHH:MM:SS' by the downloader.
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _feed_has_episodes(conn, feed_id):
    """Return True if the feed has ANY downloaded episode rows (dated or not)."""
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM episodes WHERE feed_id = ? LIMIT 1', (feed_id,))
    return cursor.fetchone() is not None


def _select_candidates(
    all_episodes,
    last_downloaded=None,
    num_episodes=None,
    since=None,
):
    """Pick the (entry, link) pairs this run should consider downloading.

    ``all_episodes`` are audio/mpeg (entry, link) pairs from the feed. Results
    are newest-first. The new-feed guard (whether a bare run may proceed at all)
    lives in the caller; this function purely applies the filters.

    Filtering order:
    1. Incremental anchor: when ``last_downloaded`` is set (feed has dated prior
       downloads), only episodes published strictly after it are candidates —
       nothing already owned is re-fetched; dateless entries are kept so catch-up
       never permanently drops them. When it is None (no dated anchor), the whole
       history is the base pool.
    2. ``since`` (datetime): keep episodes published on/after it. Dateless
       entries are excluded because an explicit window is precise.
    3. ``num_episodes``: cap to that many newest candidates (>= 1).
    """
    ordered = sorted(all_episodes, key=_episode_sort_key, reverse=True)

    if last_downloaded is not None:
        # Incremental: strictly after the cutoff; keep dateless entries too.
        base = [
            pair
            for pair in ordered
            if (dt := _entry_datetime(pair[0])) is None or dt > last_downloaded
        ]
    else:
        base = ordered

    if since is not None:
        # An explicit date window is exact: a dateless entry can't be proven to
        # fall on/after `since`, so exclude it. (Dateless entries are only kept
        # for the incremental anchor above, so catch-up never permanently drops
        # them — an explicit --since is a deliberate, precise filter.)
        base = [
            pair for pair in base if (dt := _entry_datetime(pair[0])) is not None and dt >= since
        ]

    if num_episodes is not None and num_episodes < 1:
        return []
    if num_episodes is not None:
        return base[:num_episodes]
    return base


def parse_size(value):
    """Parse human size string to bytes. Accepts 500, 500K, 500M, 2G (1024-base)."""
    s = str(value).strip().lower()
    if not s:
        raise ValueError('empty size')
    m = re.match(r'^(\d+(?:\.\d+)?)\s*([kmg]?b?)?$', s)
    if not m:
        raise ValueError(f'Invalid size: {value!r}')
    num = float(m.group(1))
    unit = (m.group(2) or '').strip()
    mult = 1
    if unit.startswith('k'):
        mult = 1024
    elif unit.startswith('m'):
        mult = 1024**2
    elif unit.startswith('g'):
        mult = 1024**3
    return int(num * mult)


def parse_max_age(value):
    """Parse age string to timedelta. Accepts '30', '30d', '30days' (days)."""
    from datetime import timedelta

    s = str(value).strip().lower()
    m = re.match(r'^(\d+)\s*(d|day|days)?$', s)
    if not m:
        raise ValueError(f'Invalid max-age: {value!r}')
    days = int(m.group(1))
    if days < 1:
        raise ValueError('max-age must be >=1 day')
    return timedelta(days=days)


def prune_feed(conn, feed_id, save_dir, keep=None, max_age=None, max_size=None, dry_run=False):
    """Flexible retention: keep N newest, drop older than max_age, cap total size.

    Args:
        keep: int or None — keep N newest (N>=1).
        max_age: timedelta, datetime, or None — drop episodes with published < now - max_age.
                 A datetime is treated as cutoff directly.
        max_size: int bytes or None — keep newest until total size <= max_size.
        dry_run: if True, log but do not delete rows/files.

    Returns (kept_count, removed_count).
    """
    from datetime import timedelta

    cursor = conn.cursor()
    cursor.execute(
        'SELECT episode_id, filepath, published FROM episodes WHERE feed_id = ? '
        'ORDER BY published DESC, episode_id DESC',
        (feed_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return (0, 0)

    # Normalize max_age to cutoff datetime
    cutoff = None
    if max_age is not None:
        if isinstance(max_age, timedelta):
            cutoff = datetime.now() - max_age
        elif isinstance(max_age, datetime):
            cutoff = max_age
        else:
            raise TypeError('max_age must be timedelta or datetime')

    # Phase 1: keep N
    if keep is not None:
        if keep < 1:
            raise ValueError('--keep must be >=1')
        kept_rows = rows[:keep]
        removed_rows = rows[keep:]
    else:
        kept_rows = list(rows)
        removed_rows = []

    # Phase 2: max-age — move aged kept rows to removed
    if cutoff is not None:
        still_kept = []
        for eid, fp, pub in kept_rows:
            if not pub:
                still_kept.append((eid, fp, pub))
                continue
            try:
                dt = datetime.fromisoformat(pub)
            except (ValueError, TypeError):
                still_kept.append((eid, fp, pub))
                continue
            if dt < cutoff:
                removed_rows.append((eid, fp, pub))
            else:
                still_kept.append((eid, fp, pub))
        kept_rows = still_kept

    # Phase 3: max-size — keep newest until size cap
    if max_size is not None:
        acc = 0
        sized_kept = []
        sized_removed = []
        for eid, fp, pub in kept_rows:
            sz = 0
            if fp and os.path.exists(fp):
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    sz = 0
            if acc + sz <= max_size or not sized_kept:
                # Always keep at least the single newest even if over cap
                sized_kept.append((eid, fp, pub))
                acc += sz
            else:
                sized_removed.append((eid, fp, pub))
        # Any removed by size joins the global removed list
        removed_rows = sized_removed + removed_rows
        kept_rows = sized_kept

    if not removed_rows:
        return (len(kept_rows), 0)

    kept_paths = {os.path.abspath(fp) for _, fp, _ in kept_rows if fp}
    if dry_run:
        logging.info(
            'DRY-RUN prune: would keep %s, remove %s (keep=%s max_age=%s max_size=%s)',
            len(kept_rows),
            len(removed_rows),
            keep,
            max_age,
            max_size,
        )
        for eid, fp, pub in removed_rows:
            logging.info('  would remove episode_id=%s filepath=%s published=%s', eid, fp, pub)
        return (len(kept_rows), len(removed_rows))

    removed = 0
    for eid, fp, _pub in removed_rows:
        cursor.execute('DELETE FROM episodes WHERE episode_id = ?', (eid,))
        removed += 1
        if fp and os.path.abspath(fp) not in kept_paths:
            try:
                if os.path.commonpath([os.path.abspath(save_dir), os.path.abspath(fp)]) == (
                    os.path.abspath(save_dir)
                ) and os.path.exists(fp):
                    os.remove(fp)
                    logging.info('Removed pruned file: %s', fp)
            except (OSError, ValueError):
                pass
    conn.commit()
    if removed:
        logging.info('Pruned %s old episode(s); keeping %s newest.', removed, len(kept_rows))
    return (len(kept_rows), removed)


def prune_to_keep_last(conn, feed_id, save_dir):
    """Keep only the newest downloaded episode row (and file) for a feed.

    Deletes older episode rows for ``feed_id`` and removes their on-disk files
    when they live under ``save_dir``. The newest row is kept as a sync reference.
    """
    prune_feed(conn, feed_id, save_dir, keep=1)


def save_text_file(entry, filename):
    """Save podcast details alongside the audio file in a ``.txt`` file."""
    # Caller passes the full audio path (e.g. /dir/ep.mp3); sidecar must be
    # /dir/ep.txt not /dir/ep.mp3.txt. Strip any existing extension first.
    base, _ext = os.path.splitext(filename)
    txt_path = f'{base}.txt' if _ext else f'{filename}.txt'
    with open(txt_path, 'w', encoding='utf-8') as file:
        file.write(f'Title: {entry.get("title", "N/A")}\n')
        file.write(f'Subtitle: {entry.get("subtitle", "N/A")}\n')
        file.write(f'Published Date: {entry.get("published", "N/A")}\n')
        file.write(f'Content: {entry.get("summary", "N/A")}\n')


def set_mp3_tags(filename, entry, feed):
    """Set MP3 tags using metadata from the RSS feed.

    Tagging is best-effort: any failure is logged and does not abort the
    remaining episodes in a run (the audio file is already safely on disk).
    """
    try:
        audio = MP3(filename, ID3=ID3)

        # Add an ID3 tag if it doesn't exist.
        if audio.tags is None:
            audio.add_tags()

        # Album (Podcast Title).
        if 'title' in feed.feed:
            audio.tags.add(TALB(encoding=3, text=feed.feed.title))

        # Artist (Podcast Author).
        artist = entry.get('author') or feed.feed.get('author')
        if artist:
            audio.tags.add(TPE1(encoding=3, text=artist))
            audio.tags.add(TPE2(encoding=3, text=artist))

        # Title (Episode Title).
        if 'title' in entry:
            audio.tags.add(TIT2(encoding=3, text=entry.title))

        # Date (Published Date).
        pub_date = _published_parsed_as_date(entry)
        if pub_date is not None:
            audio.tags.add(TDRC(encoding=3, text=pub_date.strftime('%Y-%m-%dT%H:%M:%S')))

        # Comment (Summary).
        summary = entry.get('summary')
        if summary:
            audio.tags.add(COMM(encoding=3, lang='eng', text=summary))

        # Genre from RSS category.
        if hasattr(feed.feed, 'tags') and feed.feed.tags:
            audio.tags.add(TCON(encoding=3, text=feed.feed.tags[0].term))

        audio.save()
        logging.info('Successfully set MP3 tags for: %s', filename)
    except Exception as e:  # pragma: no cover - depends on file/mp3 internals
        logging.error('Could not tag file (continuing): %s - %s', filename, e)


def _is_audio_enclosure(link):
    """Return True for podcast audio/video enclosures.

    Accepts any ``audio/*`` type (covers audio/mpeg, audio/mp3, audio/mp4,
    audio/x-mpeg, audio/ogg…) plus ``video/mp4``/``video/mpeg`` which some
    feeds use for video podcasts. Falls back to extension check when type is
    missing/empty.
    """
    t = getattr(link, 'type', None) or ''
    t = t.lower().strip()
    if t.startswith('audio/') or t in ('video/mp4', 'video/mpeg'):
        return True
    # Fallback: URL with known audio/video extension even if type is absent.
    href = getattr(link, 'href', '') or ''
    _, ext = os.path.splitext(urlparse(unquote(href)).path)
    return ext.lower() in ('.mp3', '.m4a', '.mp4', '.ogg', '.opus', '.flac', '.wav', '.aac')


def _unique_filepath(save_dir, basename, ext):
    """Return a non-colliding path inside save_dir, adding _2, _3 suffixes."""
    candidate = os.path.join(save_dir, basename + ext)
    if not os.path.exists(candidate):
        return candidate
    counter = 2
    while True:
        candidate = os.path.join(save_dir, f'{basename}_{counter}{ext}')
        if not os.path.exists(candidate):
            return candidate
        counter += 1
        # Safety cap — should never hit in practice.
        if counter > 1000:
            return candidate


def _episode_sort_key(entry_link):
    """Sort key so episodes order by publication recency, newest first."""
    entry, _link = entry_link
    dt = _entry_datetime(entry)
    # Entries without a parseable date sort to the end (epoch, oldest possible).
    return dt.timestamp() if dt else 0.0


def parse_and_download(
    save_dir,
    save_text,
    num_episodes=None,
    conn=None,
    feed_id=None,
    feed=None,
    session=None,
    since=None,
    full_history=False,
    dry_run=False,
):
    """Parse the RSS feed and download any episodes not already in the database."""
    if not conn or not feed_id or not feed:
        logging.error('Database connection, feed_id, or feed object not provided.')
        return

    cursor = conn.cursor()

    all_episodes = [
        (entry, link)
        for entry in feed.entries
        for link in entry.get('links', [])
        if _is_audio_enclosure(link)
    ]

    # Decide which episodes to consider this run. Incremental sync: only
    # episodes published strictly after the newest one already downloaded are
    # candidates once a feed has any download. --num-episodes caps the count,
    # --since sets a lower date window, --all opts into a full archive download.
    if num_episodes is not None and num_episodes < 1:
        logging.error(
            '--num-episodes must be a positive integer (got %s). Nothing to download.',
            num_episodes,
        )
        return

    last_downloaded = _get_last_downloaded_date(conn, feed_id)
    if last_downloaded is not None:
        logging.info(
            'Newest already-downloaded episode for this feed: %s',
            last_downloaded.isoformat(),
        )
    else:
        logging.info('No prior downloads for this feed.')

    if num_episodes is not None:
        logging.info(
            '--num-episodes set to %s. Considering up to %s newest unseen episodes.',
            num_episodes,
            num_episodes,
        )
    if since is not None:
        logging.info(
            '--since set to %s. Only episodes from this date are considered.', since.date()
        )

    # New-feed guard: a feed with NO downloaded rows at all must not be silently
    # fully downloaded. It needs an explicit control: --num-episodes, --since,
    # or --all. (An all-dateless feed still counts as established via row count,
    # so it is not blocked from incremental catch-up.)
    has_any = _feed_has_episodes(conn, feed_id)
    if not has_any and not full_history and num_episodes is None and since is None:
        logging.info(
            'This feed has no downloads yet. Nothing downloaded: pass --num-episodes N, '
            '--since YYYY-MM-DD, or --all to seed it (otherwise no episodes are fetched).'
        )
        return

    episodes_to_consider = _select_candidates(
        all_episodes,
        last_downloaded,
        num_episodes,
        since=since,
    )

    # Filter out episodes that have already been downloaded.
    episodes_to_download = []
    for entry, link in episodes_to_consider:
        guid = entry.get('id', link.href)
        cursor.execute('SELECT guid FROM episodes WHERE feed_id = ? AND guid = ?', (feed_id, guid))
        if not cursor.fetchone():
            episodes_to_download.append((entry, link))

    total_to_download = len(episodes_to_download)
    logging.info(
        'Found %s total episodes. Considering %s. Found %s new episodes to download.',
        len(all_episodes),
        len(episodes_to_consider),
        total_to_download,
    )

    if dry_run:
        if total_to_download == 0:
            logging.info('DRY-RUN: nothing would be downloaded.')
        else:
            logging.info('DRY-RUN: would download %s episode(s):', total_to_download)
            for entry, link in episodes_to_download:
                fn = sanitize_filename_from_entry(entry)
                logging.info('  - %s  [%s]', fn, link.href)
        return

    successful_downloads = 0
    for i, (entry, link) in enumerate(episodes_to_download):
        filename = sanitize_filename_from_entry(entry)
        # Guard against filesystem limits (255 char filename, 260+ path).
        if len(filename) > 200:
            filename = filename[:200].rstrip('_-')

        # Resolve the file extension from the URL, defaulting based on MIME.
        parsed_url = urlparse(unquote(link.href))
        _, file_extension = os.path.splitext(parsed_url.path)
        if not file_extension:
            lt = (getattr(link, 'type', '') or '').lower()
            if lt.startswith('audio/'):
                file_extension = '.mp3'
            elif lt.startswith('video/'):
                file_extension = '.mp4'
            else:
                file_extension = '.mp3'

        full_path = _unique_filepath(save_dir, filename, file_extension)

        logging.info('Downloading audio file %s of %s: %s', i + 1, total_to_download, filename)
        if download_file(link.href, full_path, session=session):
            guid = entry.get('id', link.href)
            pub_date = _published_parsed_as_date(entry)
            published_iso = pub_date.strftime('%Y-%m-%dT%H:%M:%S') if pub_date else ''

            try:
                cursor.execute(
                    'INSERT INTO episodes '
                    '(feed_id, guid, title, published, filepath, downloaded_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (
                        feed_id,
                        guid,
                        entry.title,
                        published_iso,
                        full_path,
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()
                successful_downloads += 1
            except sqlite3.IntegrityError:
                logging.warning(
                    'Episode with GUID %s already in database for this feed. Skipping DB entry.',
                    guid,
                )
                continue

            # Set MP3 tags.
            if full_path.lower().endswith('.mp3'):
                set_mp3_tags(full_path, entry, feed)

            if save_text:
                save_text_file(entry, full_path)

        if i < total_to_download - 1:
            logging.info('Sleeping for 1 second...')
            time.sleep(1)

    logging.info(
        'Completed! Successfully downloaded %s / %s audio files',
        successful_downloads,
        total_to_download,
    )


def main():
    parser = argparse.ArgumentParser(description='RSS Podcast Downloader')
    parser.add_argument('rss_url', nargs='?', help='RSS feed URL (omit if using --feed-id)')
    parser.add_argument('save_dir', nargs='?', help='Directory to save downloaded files')
    parser.add_argument(
        '--feed-id',
        type=int,
        default=None,
        help='Pull a feed already stored in the database by its id (overrides rss_url)',
    )
    parser.add_argument(
        '--list-feeds', action='store_true', help='List all feeds in the database and exit'
    )
    parser.add_argument(
        '--keep-last',
        action='store_true',
        help='After downloading, keep only the newest episode per feed as a sync reference',
    )
    parser.add_argument(
        '--keep',
        type=int,
        default=None,
        metavar='N',
        help='Keep only N newest episodes per feed (prunes older files/rows)',
    )
    parser.add_argument(
        '--max-age',
        default=None,
        metavar='DAYS',
        help='Keep only episodes newer than DAYS days (e.g. 30, 30d). Dateless kept.',
    )
    parser.add_argument(
        '--max-size',
        default=None,
        metavar='SIZE',
        help='Keep newest episodes until total size <= SIZE (e.g. 500M, 2G, 1024).',
    )
    parser.add_argument(
        '--save_text', action='store_true', help='Save .txt files with extra episode data'
    )
    parser.add_argument(
        '--num-episodes',
        type=int,
        default=None,
        help='Only download up to N newest episodes published after the last downloaded one',
    )
    parser.add_argument(
        '--since',
        default=None,
        metavar='YYYY-MM-DD',
        help='Only download episodes published on or after this date',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='On a feed with no prior downloads, download the full archive',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be downloaded without downloading or writing DB',
    )
    parser.add_argument('--verbose', action='store_true', help='Verbose logging (DEBUG)')
    parser.add_argument('--quiet', action='store_true', help='Quiet logging (WARNING only)')
    parser.add_argument(
        '--remove-feed', type=int, default=None, metavar='ID', help='Remove feed by ID and exit'
    )
    parser.add_argument(
        '--delete-files',
        action='store_true',
        help='With --remove-feed, also delete episode files on disk',
    )
    parser.add_argument(
        '--export-opml', default=None, metavar='FILE', help='Export feeds to OPML file and exit'
    )
    parser.add_argument(
        '--import-opml', default=None, metavar='FILE', help='Import feeds from OPML file and exit'
    )
    parser.add_argument('--config', default=None, metavar='FILE', help='Config file path (TOML)')
    parser.add_argument('--no-config', action='store_true', help='Disable config file loading')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    # Load config file before final parse so CLI overrides config
    # Peek sys.argv for --config / --no-config
    _explicit_cfg = None
    _no_cfg = '--no-config' in sys.argv
    if not _no_cfg:
        for _i, _a in enumerate(sys.argv):
            if _a == '--config' and _i + 1 < len(sys.argv):
                _explicit_cfg = sys.argv[_i + 1]
                break
            if _a.startswith('--config='):
                _explicit_cfg = _a.split('=', 1)[1]
                break
        _cfg_path = find_config_path(_explicit_cfg, no_config=_no_cfg)
        _cfg = load_config(_cfg_path)
        if _cfg:
            # Only apply known dest names
            _allowed = {
                'save_dir',
                'keep',
                'max_age',
                'max_size',
                'verbose',
                'quiet',
                'save_text',
                'num_episodes',
                'since',
                'all',
                'keep_last',
                'dry_run',
                'feed_id',
            }
            _filtered = {k: v for k, v in _cfg.items() if k in _allowed}
            if _filtered:
                parser.set_defaults(**_filtered)
                logging.debug('Loaded config %s: %s', _cfg_path, _filtered)

    args = parser.parse_args()

    # Configure logging verbosity (default INFO).
    if args.verbose and args.quiet:
        parser.error('--verbose and --quiet are mutually exclusive')
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    # Validate retention flags
    if args.keep is not None and args.keep_last:
        parser.error('--keep and --keep-last are mutually exclusive')
    if args.keep is not None and args.keep < 1:
        parser.error(f'--keep must be a positive integer (got {args.keep})')
    max_age_delta = None
    if args.max_age:
        try:
            max_age_delta = parse_max_age(args.max_age)
        except ValueError as e:
            parser.error(str(e))
    max_size_bytes = None
    if args.max_size:
        try:
            max_size_bytes = parse_size(args.max_size)
        except ValueError as e:
            parser.error(str(e))
        if max_size_bytes < 1:
            parser.error('--max-size must be >=1 byte')

    if args.delete_files and args.remove_feed is None:
        parser.error('--delete-files requires --remove-feed')

    if args.list_feeds:
        list_feeds()
        return

    db_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'downloads.db')

    if args.remove_feed is not None:
        conn = setup_database(db_path)
        try:
            ok = remove_feed(conn, args.remove_feed, delete_files=args.delete_files)
        finally:
            conn.close()
        if not ok:
            parser.error(f'No feed found with id {args.remove_feed}')
        print(f'Removed feed {args.remove_feed}')
        return

    if args.export_opml:
        count = export_opml(db_path, args.export_opml)
        print(f'Exported {count} feed(s) to {args.export_opml}')
        return

    if args.import_opml:
        imported, skipped = import_opml(db_path, args.import_opml)
        print(f'Imported {imported} feed(s), skipped {skipped} existing')
        return

    # Parse --since into a naive datetime (midnight local) for comparison.
    since_date = None
    if args.since:
        try:
            since_date = datetime.strptime(args.since, '%Y-%m-%d')
        except ValueError:
            parser.error('--since must be a date in YYYY-MM-DD format')

    # When --feed-id is used the URL positional is not needed, so a lone
    # positional argument is interpreted as save_dir rather than rss_url.
    if args.feed_id is not None and args.save_dir is None and args.rss_url is not None:
        args.save_dir = args.rss_url
        args.rss_url = None

    # Resolve the feed URL: --feed-id (from DB) wins over the positional rss_url.
    if args.feed_id is not None:
        # setup_database() ensures the schema (incl. save_dir) exists first.
        lookup = setup_database(db_path)
        try:
            stored_url = get_feed_url_by_id(lookup, args.feed_id)
            stored_dir = get_feed_save_dir(lookup, args.feed_id)
        finally:
            lookup.close()
        if not stored_url:
            parser.error(f'No feed found with --feed-id {args.feed_id} in the database.')
        args.rss_url = stored_url
        # If the user gave no folder, fall back to the feed's stored one.
        if args.save_dir is None:
            if stored_dir:
                args.save_dir = stored_dir
                logging.info(
                    'Using stored save directory for feed %s: %s', args.feed_id, stored_dir
                )
            else:
                parser.error(
                    f'No save directory given and feed {args.feed_id} has none stored. '
                    'Provide a save_dir or run once with one to persist it.'
                )

    if not args.rss_url:
        parser.error('rss_url is required unless --feed-id is used')

    if not args.save_dir:
        parser.error('save_dir is required on the first download for a feed')

    conn = None
    try:
        # Create the save directory if it doesn't exist.
        os.makedirs(args.save_dir, exist_ok=True)
        content = fetch_rss_feed(args.rss_url)
        if content:
            feed = feedparser.parse(content)
            conn = setup_database()
            feed_id = get_or_create_feed(
                conn, args.rss_url, feed.feed.get('title', 'N/A'), save_dir=args.save_dir
            )
            parse_and_download(
                args.save_dir,
                args.save_text,
                args.num_episodes,
                conn=conn,
                feed_id=feed_id,
                feed=feed,
                since=since_date,
                full_history=args.all,
                dry_run=args.dry_run,
            )
            # Retention pruning (flexible or legacy keep-last)
            if (
                args.keep is not None
                or args.keep_last
                or max_age_delta is not None
                or max_size_bytes is not None
            ):
                keep_n = args.keep if args.keep is not None else (1 if args.keep_last else None)
                prune_feed(
                    conn,
                    feed_id,
                    args.save_dir,
                    keep=keep_n,
                    max_age=max_age_delta,
                    max_size=max_size_bytes,
                    dry_run=args.dry_run,
                )
    except Exception as e:
        logging.error('An unexpected error occurred: %s', e, exc_info=True)
    finally:
        if conn:
            conn.close()
            logging.info('Database connection closed.')


if __name__ == '__main__':
    main()
