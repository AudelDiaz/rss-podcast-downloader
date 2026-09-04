# Feature: Installable Package + Config File

## Problem Statement
Today it is a script invoked as `python rss-podcast-downloader.py URL dir` with `requirements.txt` and no `[project]`. It is not installable via `pipx`/`uv tool`, does not expose `console_scripts`, and forces repeating `URL save_dir --flags` on every cron/systemd/Docker invocation.

## Background
- Single-file CLI `rss-podcast-downloader.py` (hyphen) is not importable as a module.
- `pyproject.toml` only has `[tool.ruff]`/`[tool.pytest]`; no `[project]`/`[build-system]`.
- No config file; flags must be repeated. Stack uses `uv` and Python 3.14 (tomllib available).

## Requirements
- **REQ-1: Packaging** — Add `[project]` (name `rss-podcast-downloader`, version `1.1.0`, deps `requests`, `feedparser`, `mutagen`), `[build-system]` (hatchling), `[project.scripts]` `rss-podcast-downloader = rss_podcast_downloader:main`.
- **REQ-2: Importable shim** — Create `rss_podcast_downloader.py` (underscore) importable that re-exports `main` and public API by dynamically loading `rss-podcast-downloader.py` (avoids duplicating 1000 lines; keeps backward compat with `python rss-podcast-downloader.py`).
- **REQ-3: Config file** — Support `TOML` with priority: explicit `--config FILE` > `./rss-podcast-downloader.toml` > `$XDG_CONFIG_HOME/rss-podcast-downloader/config.toml` > `~/.config/rss-podcast-downloader/config.toml`. If not exists, no error.
- **REQ-4: Config schema** — Section `[defaults]` with keys mapped to flags: `save_dir`, `keep`, `max_age`, `max_size`, `verbose`, `quiet`, `save_text`, `num_episodes`, `since`, `all`. CLI values always override config.
- **REQ-5: CLI `--config` / `--no-config`** — `--config FILE` forces path; `--no-config` disables loading.
- **REQ-6: Compat** — Installation `pip install .` or `uv tool install .` must create bin `rss-podcast-downloader` working without `python` prefix; `python rss-podcast-downloader.py` keeps working.

### Scenarios
```gherkin
Feature: install
  Scenario: pip install
    When pip install .
    Then command rss-podcast-downloader --help works

Feature: config
  Scenario: defaults from file
    Given ./rss-podcast-downloader.toml with [defaults] keep=5
    When rss-podcast-downloader <url> <dir> (without --keep)
    Then keep=5 is applied
    When rss-podcast-downloader <url> <dir> --keep 2
    Then CLI overrides to 2

  Scenario: --no-config ignores file
    When --no-config
    Then config is not loaded
```

## Architecture
### Pyproject
```toml
[project]
name = "rss-podcast-downloader"
version = "1.1.0"
requires-python = ">=3.10"
dependencies = ["requests==2.32.0","feedparser==6.0.11","mutagen==1.48.1","certifi","charset-normalizer","idna","urllib3","sgmllib3k"]
[project.scripts]
rss-podcast-downloader = "rss_podcast_downloader:main"
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Shim
`rss_podcast_downloader.py`:
```python
import importlib.util, pathlib, sys

_spec = importlib.util.spec_from_file_location(
    'rpd_impl', pathlib.Path(__file__).parent / 'rss-podcast-downloader.py'
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
main = _mod.main
# re-export API for tests/consumers
```

### Config loader
- `find_config_path(explicit=None, no_config=False) -> Path|None` — resolves priority.
- `load_config(path) -> dict` — uses `tomllib` (py314) fallback `tomli`; returns `{}` if missing; logs warning if parse error.
- In `main()`, before `parser.parse_args()`, load config, then `parser.set_defaults(**config_defaults)` so CLI overrides.

## API / Interface
- `find_config_path`, `load_config`, `get_config_defaults`
- `rss_podcast_downloader:main` entry point

## Testing Strategy
- Unit: config priority, toml parse, defaults merging, --no-config, shim import.
- Integration: `pip install` smoke (help).

## Out of Scope
- Migration of DB to config.
- YAML support (only TOML stdlib).
