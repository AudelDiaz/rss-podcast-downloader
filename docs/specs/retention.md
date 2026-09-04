# Feature: Retención Flexible (--keep / --max-age / --max-size)

## Problem Statement
Hoy solo existe `--keep-last` (mantener 1 episodio como referencia de sync). Para uso "set and forget" el disco se llena; usuarios necesitan "últimos N", "borrar >30 días" o "no superar 2GB por feed" sin editar la DB manualmente.

## Background
- `prune_to_keep_last(conn, feed_id, save_dir)` mantiene el más nuevo (`ORDER BY published DESC, episode_id DESC`), borra filas y ficheros bajo `save_dir`, protege ficheros compartidos entre filas.
- `episodes.published` formato `YYYY-MM-DDTHH:MM:SS` o `''` (dateless). `episodes.filepath` absoluto.
- Flags existentes: `--keep-last`, `--num-episodes`, `--since`, `--all`. Nuevos flags deben componerse con ellos y respetar `--dry-run`.

## Requirements
- **REQ-1: `--keep N`** — tras descargar, mantener solo los N episodios más nuevos (N>=1). Reemplaza/augmenta `--keep-last` (`--keep-last` ≡ `--keep 1`, ambos mutuamente excluyentes).
- **REQ-2: `--max-age DAYS` o `30d`/`30`** — borrar episodios con `published` estrictamente anterior a `now - DAYS`. Dateless (`published=''`) se conserva (no evaluable). Ficheros huérfanos fuera de `save_dir` nunca se borran.
- **REQ-3: `--max-size SIZE`** — cuota por feed: mantener episodio más nuevo primero, acumular `os.path.getsize(filepath)` hasta superar `SIZE` (ej. `500M`, `2G`, `1024`). El resto se purga. Si un fichero no existe, tamaño 0 pero fila se borra igual.
- **REQ-4: Composición** — filtros se aplican en orden: `keep` → `max-age` → `max-size` sobre el set ordenado. Todas las purgas comparten protección de ficheros compartidos y `commonpath(save_dir)`.
- **REQ-5: Dry-run** — con `--dry-run` no se escribe DB ni se borran ficheros; loggear qué se purgaría.
- **REQ-6: Backward compat** — `--keep-last` sigue funcionando; si se pasa `--keep` y `--keep-last` error claro.

### Scenarios
```gherkin
Feature: keep N
  Scenario: keep 2 de 5
    Given 5 episodios 2020..2024
    When --keep 2
    Then quedan los 2 más nuevos, 3 filas y sus ficheros borrados

Feature: max-age
  Scenario: borrar >30 días
    Given hoy 2025-06-15, episodios 2025-05-01 y 2025-06-10
    When --max-age 30
    Then solo 2025-06-10 sobrevive; dateless sobrevive

Feature: max-size
  Scenario: cuota 1KB
    Given 3 ficheros 600B cada uno (nuevo, medio, viejo)
    When --max-size 1KB
    Then solo el más nuevo (600B) queda; los demás se purgan
```

## Architecture
### Helpers
- `parse_size(value: str) -> int` — parsea `"500M"/"2G"/"1024"` → bytes (K=1024, M, G). Case-insensitive, acepta `B` suffix opcional.
- `parse_max_age(value: str) -> timedelta` — acepta `"30"`, `"30d"`, `"30days"` → `timedelta(days=30)`.
- `prune_feed(conn, feed_id, save_dir, keep=None, max_age=None, max_size=None, dry_run=False)` — núcleo testable. Retorna `(kept, removed)`. Internamente:
  1. `SELECT episode_id, filepath, published FROM episodes WHERE feed_id=? ORDER BY published DESC, episode_id DESC`
  2. Determinar `to_keep` inicial (primer `keep` filas si `keep` else todo).
  3. Filtrar `max_age`: para cada fila en `to_keep`, si `published` parseable y `published < cutoff` → mover a `to_remove` (dateless nunca se mueve por edad).
  4. Filtrar `max_size`: iterar `to_keep` en orden, acumular tamaños, cortar cuando `acc > max_size`.
  5. Proteger shared paths, borrar con `commonpath` guard.

- `prune_to_keep_last(...)` pasa a ser wrapper `prune_feed(..., keep=1)`.

### CLI
- `--keep int >=1`, `--max-age str`, `--max-size str`, ya validados en `main()` vía parsers; error con `parser.error` si inválido.
- Orden en `main()`: descargar → `prune_feed` si algún flag presente; `--dry-run` propaga a `prune_feed`.

## API / Interface
- `prune_feed(conn, feed_id, save_dir, keep=None, max_age=None, max_size=None, dry_run=False) -> tuple[int,int]`
- `prune_to_keep_last(conn, feed_id, save_dir)` — wrapper compat.
- `parse_size(s: str) -> int`, `parse_max_age(s: str) -> timedelta`.

## Testing Strategy
- Unit: `keep N` conserva N más nuevos; `max-age` respeta dateless; `max-size` con ficheros de tamaño conocido; `keep+max-age` composición; `shared file` no se borra si fila kept lo referencia; `dry_run` no borra.
- Integration: flujo completo con HTTP fixture + ficheros reales de tamaños distintos.

## Out of Scope
- Cuota global multi-feed.
- Mover ficheros a otro directorio al purgar.
