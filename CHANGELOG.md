# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-09-04

### Added
- **Flexible retention**: flags `--keep N`, `--max-age DAYS` (e.g. `30d`), `--max-size SIZE` (e.g. `500M`, `2G`) via `prune_feed()`. Composable; protects shared files and respects `commonpath(save_dir)`. `prune_to_keep_last` is now a `keep=1` wrapper.
- **CRUD feeds + OPML**: `remove_feed()`, `export_opml()`, `import_opml()` with CLI `--remove-feed ID [--delete-files]`, `--export-opml FILE`, `--import-opml FILE`.
- **Installable packaging**: `pyproject.toml` `[project]` + `[build-system] hatchling` + `[project.scripts] rss-podcast-downloader = rss_podcast_downloader:main`; shim `rss_podcast_downloader.py` with dynamic loading of hyphen script.
- **TOML config file**: `find_config_path()` / `load_config()` with priority `--config` > `./rss-podcast-downloader.toml` > `XDG_CONFIG_HOME` > `~/.config/rss-podcast-downloader/config.toml`; `[defaults]` section; CLI overrides config. Flags `--config FILE`, `--no-config`. Example in `config.example.toml`.
- **Sync UX**: `--dry-run` (lists without downloading or writing DB), `audio/*` + `video/mp4` support (previously only `audio/mpeg`) via `_is_audio_enclosure()`, anti-collision `_unique_filepath()` suffix `_2/_3`, `--verbose`/`--quiet`, filename truncation >200 chars.
- **Versioning**: `__version__ = '1.1.0'` + flag `--version` + versioned `USER_AGENT`.

### Fixed
- **FP-1**: `download_file` stores malformed `Content-Length` (`ValueError`) as `None` instead of aborting batch.
- **FP-3**: `download_file` accepts injectable `sleep_fn` for tests without `time.sleep`.
- **FP-4**: `save_text_file` generates `ep.txt` not `ep.mp3.txt` (strip ext) + `encoding='utf-8'`.
- **FP-5**: Reproducible pins `mutagen==1.48.1`, `pytest==9.1.1`, `ruff==0.16.6` in `requirements.txt` and `ci.yml`; `pyproject.toml:target-version py310`.

### Changed
- `pyproject.toml` is now source of truth for dependencies and build; `rss_podcast_downloader.py` is an importable module for `pipx`/`uv tool`.

### Specs
- New specs: `docs/specs/retention.md`, `docs/specs/feed-crud-opml.md`, `docs/specs/packaging-config.md`.

## [1.0.0] - 2026-02-02
- First packageable version with stateful DB, multi-feed tracking and initial tests.

## [Unreleased]
- See `git log` for unreleased changes.
