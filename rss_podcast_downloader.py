"""Shim importable module for packaging.

Loads the hyphenated ``rss-podcast-downloader.py`` script dynamically
and re-exports its public API. This keeps a single source of truth
(the hyphen file) while making the package installable via
``pip install .`` / ``uv tool`` with entry point
``rss-podcast-downloader = rss_podcast_downloader:main``.
"""

import importlib.util
import pathlib
import sys

_HYPHEN = pathlib.Path(__file__).with_name('rss-podcast-downloader.py')
if not _HYPHEN.exists():
    _HYPHEN = pathlib.Path(__file__).parent / 'rss-podcast-downloader.py'
if not _HYPHEN.exists():
    raise FileNotFoundError(f'Hyphen script not found: {_HYPHEN} (required for shim)')

_spec = importlib.util.spec_from_file_location('rpd_impl', _HYPHEN)
if _spec is None or _spec.loader is None:
    raise ImportError(f'Cannot load spec for {_HYPHEN}')
_mod = importlib.util.module_from_spec(_spec)
sys.modules['rpd_impl'] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[attr-defined]

__version__ = getattr(_mod, '__version__', None)
if __version__ is None:
    raise ImportError(f'__version__ not found in {_HYPHEN}')

# Re-export public API
main = _mod.main
setup_database = _mod.setup_database
get_or_create_feed = _mod.get_or_create_feed
get_feed_url_by_id = _mod.get_feed_url_by_id
get_feed_save_dir = _mod.get_feed_save_dir
set_feed_save_dir = _mod.set_feed_save_dir
list_feeds = _mod.list_feeds
remove_feed = _mod.remove_feed
export_opml = _mod.export_opml
import_opml = _mod.import_opml
prune_feed = _mod.prune_feed
prune_to_keep_last = _mod.prune_to_keep_last
parse_size = _mod.parse_size
parse_max_age = _mod.parse_max_age
download_file = _mod.download_file
fetch_rss_feed = _mod.fetch_rss_feed
sanitize_title = _mod.sanitize_title
sanitize_filename_from_entry = _mod.sanitize_filename_from_entry
entry_date_prefix = _mod.entry_date_prefix
save_text_file = _mod.save_text_file
set_mp3_tags = _mod.set_mp3_tags
parse_and_download = _mod.parse_and_download
find_config_path = getattr(_mod, 'find_config_path', None)
load_config = getattr(_mod, 'load_config', None)


def __getattr__(name):  # PEP 562
    return getattr(_mod, name)


if __name__ == '__main__':
    main()
