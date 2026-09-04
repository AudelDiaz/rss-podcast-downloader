"""RSS Podcast Downloader.

Description: This script downloads audio files (and optionally accompanying text
information) from a specified RSS feed, writing clean, portable filenames and
enriching the media library with episode metadata (title, album, artist) on the
downloaded MP3 files.

A local SQLite database (downloads.db) tracks all downloaded episodes so reruns
skip already-fetched episodes and multiple feeds can be managed independently.

Usage:
    python rss-podcast-downloader.py <RSS_FEED_URL> <SAVE_DIRECTORY> [--save_text]

Options:
    --num-episodes N   Only consider the N most recently published episodes.
    --save_text        Also save a .txt sidecar with episode details.

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
import time
import unicodedata
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlparse

import feedparser
import requests
from mutagen.id3 import COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2
from mutagen.mp3 import MP3

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

USER_AGENT = 'rss-podcast-downloader/1.0 (+https://github.com/johnsosoka/rss-podcast-downloader)'

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


def download_file(url, filename, session=None, retries=3):
    """Stream a file from ``url`` to ``filename`` with retry/backoff logic.

    Returns ``True`` on success. A partial file is removed before each retry so
    a truncated download is never mistaken for a complete one.
    """
    session = session if session is not None else requests
    headers = {'User-Agent': USER_AGENT}

    for attempt in range(1, retries + 1):
        try:
            with session.get(url, headers=headers, stream=True, timeout=60) as response:
                response.raise_for_status()
                # Where the server declares a length, verify we received all of it
                # so a clean-but-truncated body is still treated as a failure.
                expected = response.headers.get('Content-Length')
                if expected is not None:
                    expected = int(expected)
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
                time.sleep(sleep_time)
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
            feed_title TEXT
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
    conn.commit()
    logging.info('Database setup complete at %s', db_path)
    return conn


def get_or_create_feed(conn, feed_url, feed_title):
    """Get the ``feed_id`` for a ``feed_url``, creating the feed if it doesn't exist."""
    cursor = conn.cursor()
    cursor.execute('SELECT feed_id FROM feeds WHERE feed_url = ?', (feed_url,))
    result = cursor.fetchone()
    if result:
        return result[0]
    cursor.execute('INSERT INTO feeds (feed_url, feed_title) VALUES (?, ?)', (feed_url, feed_title))
    conn.commit()
    logging.info('Added new feed to database: %s', feed_title)
    return cursor.lastrowid


def save_text_file(entry, filename):
    """Save podcast details alongside the audio file in a ``.txt`` file."""
    with open(f'{filename}.txt', 'w') as file:
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
        if link.type == 'audio/mpeg'
    ]

    # Consider only the N most recently published episodes.
    episodes_to_consider = all_episodes
    if num_episodes is not None:
        if num_episodes < 1:
            logging.error(
                '--num-episodes must be a positive integer (got %s). Nothing to download.',
                num_episodes,
            )
            return
        logging.info(
            '--num-episodes set to %s. Considering only the %s most recent episodes.',
            num_episodes,
            num_episodes,
        )
        ordered = sorted(all_episodes, key=_episode_sort_key, reverse=True)
        episodes_to_consider = ordered[:num_episodes]

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

    successful_downloads = 0
    for i, (entry, link) in enumerate(episodes_to_download):
        filename = sanitize_filename_from_entry(entry)

        # Resolve the file extension from the URL, defaulting to .mp3 for audio.
        parsed_url = urlparse(unquote(link.href))
        _, file_extension = os.path.splitext(parsed_url.path)
        if not file_extension and link.type == 'audio/mpeg':
            file_extension = '.mp3'

        full_path = os.path.join(save_dir, filename + file_extension)

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
    parser.add_argument('rss_url', help='RSS feed URL (include authentication token if applicable)')
    parser.add_argument('save_dir', help='Directory to save downloaded files')
    parser.add_argument(
        '--save_text', action='store_true', help='Save .txt files with extra episode data'
    )
    parser.add_argument(
        '--num-episodes',
        type=int,
        default=None,
        help='Only download from the N most recently published episodes',
    )
    args = parser.parse_args()

    conn = None
    try:
        # Create the save directory if it doesn't exist.
        os.makedirs(args.save_dir, exist_ok=True)
        content = fetch_rss_feed(args.rss_url)
        if content:
            feed = feedparser.parse(content)
            conn = setup_database()
            feed_id = get_or_create_feed(conn, args.rss_url, feed.feed.get('title', 'N/A'))
            parse_and_download(
                args.save_dir,
                args.save_text,
                args.num_episodes,
                conn=conn,
                feed_id=feed_id,
                feed=feed,
            )
    except Exception as e:
        logging.error('An unexpected error occurred: %s', e, exc_info=True)
    finally:
        if conn:
            conn.close()
            logging.info('Database connection closed.')


if __name__ == '__main__':
    main()
