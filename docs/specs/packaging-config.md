# Feature: Empaquetado Instalable + Archivo Config

## Problem Statement
Hoy es un script con guion `python rss-podcast-downloader.py URL dir` y `requirements.txt` sin `[project]`. No es instalable vía `pipx`/`uv tool`, no expone `console_scripts`, y obliga a repetir `URL save_dir --flags` en cada cron/systemd/Docker.

## Background
- Single-file CLI `rss-podcast-downloader.py` (hyphen) no es importable como módulo.
- `pyproject.toml` solo tiene `[tool.ruff]`/`[tool.pytest]`; sin `[project]`/`[build-system]`.
- No hay config file; flags deben repetirse. Stack usa `uv` y Python 3.14 (tomllib disponible).

## Requirements
- **REQ-1: Packaging** — Añadir `[project]` (name `rss-podcast-downloader`, version `1.1.0`, deps `requests`, `feedparser`, `mutagen`), `[build-system]` (hatchling), `[project.scripts]` `rss-podcast-downloader = rss_podcast_downloader:main`.
- **REQ-2: Shim importable** — Crear `rss_podcast_downloader.py` (underscore) importable que re-exporta `main` y API pública cargando dinámicamente `rss-podcast-downloader.py` (evita duplicar 1000 líneas; mantiene compat backward con invocación `python rss-podcast-downloader.py`).
- **REQ-3: Config file** — Soportar `TOML` en prioridad: `--config FILE` explícito > `./rss-podcast-downloader.toml` > `$XDG_CONFIG_HOME/rss-podcast-downloader/config.toml` > `~/.config/rss-podcast-downloader/config.toml`. Si no existe, sin error.
- **REQ-4: Config schema** — Sección `[defaults]` con claves mapeadas a flags: `save_dir`, `keep`, `max_age`, `max_size`, `verbose`, `quiet`, `save_text`, `num_episodes`, `since`, `all`. Valores CLI siempre override config.
- **REQ-5: CLI `--config` / `--no-config`** — `--config FILE` fuerza path; `--no-config` deshabilita carga.
- **REQ-6: Compat** — Instalación `pip install .` o `uv tool install .` debe crear bin `rss-podcast-downloader` que funciona sin `python` prefix; `python rss-podcast-downloader.py` sigue funcionando.

### Scenarios
```gherkin
Feature: install
  Scenario: pip install
    When pip install .
    Then command rss-podcast-downloader --help works

Feature: config
  Scenario: defaults desde fichero
    Given ./rss-podcast-downloader.toml con [defaults] keep=5
    When rss-podcast-downloader <url> <dir> (sin --keep)
    Then keep=5 se aplica
    When rss-podcast-downloader <url> <dir> --keep 2
    Then CLI override a 2

  Scenario: --no-config ignora fichero
    When --no-config
    Then config no se carga
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
- `find_config_path(explicit=None, no_config=False) -> Path|None` — resuelve prioridad.
- `load_config(path) -> dict` — usa `tomllib` (py314) fallback `tomli`; retorna `{}` si missing; log warning si parse error.
- En `main()`, antes de `parser.parse_args()`, cargar config, luego `parser.set_defaults(**config_defaults)` para que CLI override.

## API / Interface
- `find_config_path`, `load_config`, `get_config_defaults`
- `rss_podcast_downloader:main` entry point

## Testing Strategy
- Unit: config priority, toml parse, defaults merging, --no-config, shim import.
- Integration: `pip install` smoke (help).

## Out of Scope
- Migración de DB a config.
- Soporte YAML (solo TOML stdlib).
