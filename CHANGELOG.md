# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-09-04

### Added
- **Retention flexible**: flags `--keep N`, `--max-age DAYS` (e.g. `30d`), `--max-size SIZE` (e.g. `500M`, `2G`) via `prune_feed()`. Composable; protege ficheros compartidos y respeta `commonpath(save_dir)`. `prune_to_keep_last` ahora es wrapper `keep=1`.
- **CRUD feeds + OPML**: `remove_feed()`, `export_opml()`, `import_opml()` con CLI `--remove-feed ID [--delete-files]`, `--export-opml FILE`, `--import-opml FILE`.
- **Packaging instalable**: `pyproject.toml` `[project]` + `[build-system] hatchling` + `[project.scripts] rss-podcast-downloader = rss_podcast_downloader:main`; shim `rss_podcast_downloader.py` carga dinámica del script con guion.
- **Config file TOML**: `find_config_path()` / `load_config()` con prioridad `--config` > `./rss-podcast-downloader.toml` > `XDG_CONFIG_HOME` > `~/.config/rss-podcast-downloader/config.toml`; sección `[defaults]`; CLI overridea config. Flags `--config FILE`, `--no-config`. Ejemplo en `config.example.toml`.
- **Sync UX**: `--dry-run` (lista sin descargar ni escribir DB), soporte `audio/*` + `video/mp4` (antes solo `audio/mpeg`) vía `_is_audio_enclosure()`, anti-colisión `_unique_filepath()` sufijo `_2/_3`, `--verbose`/`--quiet`, truncado de filename >200 chars.
- **Versioning**: `__version__ = '1.1.0'` + flag `--version` + `USER_AGENT` versionado.

### Fixed
- **FP-1**: `download_file` guarda `Content-Length` malformado (`ValueError`) como `None` en vez de abortar lote.
- **FP-3**: `download_file` acepta `sleep_fn` inyectable para tests sin `time.sleep`.
- **FP-4**: `save_text_file` genera `ep.txt` no `ep.mp3.txt` (strip ext) + `encoding='utf-8'`.
- **FP-5**: Pins reproducibles `mutagen==1.48.1`, `pytest==9.1.1`, `ruff==0.16.6` en `requirements.txt` y `ci.yml`; `pyproject.toml:target-version py310`.

### Changed
- `pyproject.toml` ahora es fuente de verdad para dependencias y build; `rss_podcast_downloader.py` es módulo importable para `pipx`/`uv tool`.

### Specs
- Nuevos specs: `docs/specs/retention.md`, `docs/specs/feed-crud-opml.md`, `docs/specs/packaging-config.md`.

## [1.0.0] - 2026-02-02
- Primera versión empaquetable con DB stateful, tracking multi-feed y tests iniciales.

## [Unreleased]
- Ver `git log` para cambios no publicados.
