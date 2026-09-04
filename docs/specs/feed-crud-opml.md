# Feature: CRUD Feeds + OPML Import/Export

## Problem Statement
Solo se puede añadir feeds implícitamente y listarlos. No hay forma de borrar un feed, exportar la colección para backup/migración, ni importar OPML desde otros podcatchers (AntennaPod, Pocket Casts) sin editar `downloads.db` manual.

## Background
- DB: `feeds(feed_id, feed_url UNIQUE, feed_title, save_dir)`, `episodes(feed_id FK, guid UNIQUE per feed, filepath)`
- `list_feeds()` ya existe; `get_or_create_feed`, `get_feed_url_by_id`, `get_feed_save_dir` existen.
- OPML es estándar: `<opml><body><outline type="rss" xmlUrl="..." text="..." /></body></opml>`

## Requirements
- **REQ-1: `--remove-feed ID`** — borra feed y sus episodios de la DB; ficheros bajo `save_dir` solo se borran si también se pasa `--delete-files`. Sin flag, filas se borran pero ficheros quedan huérfanos (log). Error claro si ID no existe.
- **REQ-2: `--export-opml FILE`** — vuelca todos los feeds a OPML 2.0 (incluye `xmlUrl`, `text`/`title`, `htmlUrl` opcional). Crea/overwrite FILE. Compatible con importación externa.
- **REQ-3: `--import-opml FILE`** — lee OPML, por cada `outline` con `xmlUrl` crea feed si no existe (usa `text`/`title` como `feed_title`). Reporta cuántos nuevos vs ya existentes. No descarga episodios.
- **REQ-4: Composición CLI** — estos flags son operaciones exclusivas: no requieren `rss_url`/`save_dir` ni descargan. Si se combinan con otros flags de sync, se ejecutan y terminan (return). `--list-feeds` ya sigue ese patrón.
- **REQ-5: `--delete-files` solo válido con `--remove-feed`** — validación via `parser.error`.

### Scenarios
```gherkin
Feature: remove feed
  Scenario: borrar feed 2 sin ficheros
    Given feed 2 con 3 episodios
    When --remove-feed 2
    Then feed 2 y sus 3 filas desaparecen; ficheros quedan en disco

  Scenario: borrar con --delete-files
    When --remove-feed 2 --delete-files
    Then ficheros bajo save_dir también se borran (si existen y bajo save_dir)

Feature: OPML
  Scenario: exportar
    When --export-opml feeds.opml
    Then FILE contiene <outline> por cada feed con xmlUrl

  Scenario: importar
    Given OPML con 2 urls, una ya existe
    When --import-opml feeds.opml
    Then 1 feed nuevo creado, 1 skipped
```

## Architecture
### Helpers
- `remove_feed(conn, feed_id, save_dir=None, delete_files=False) -> bool` — borra episodios y feed; si delete_files y save_dir dado, borra ficheros con `commonpath` guard + protección shared-kept no necesaria (todo feed se borra).
- `export_opml(db_path, output_path)` — query `SELECT feed_url, feed_title FROM feeds`, genera XML con `xml.etree.ElementTree`, escribe UTF-8 con header `<?xml ...>` y `<opml version="2.0">`.
- `import_opml(db_path, input_path, conn=None) -> (imported, skipped)` — parsea OPML, itera `outline` recursivo, extrae `xmlUrl`, `text`/`title`, llama `get_or_create_feed` (detecta skip por URL existente).

### CLI
- `parser.add_argument('--remove-feed', type=int)`, `'--export-opml'`, `'--import-opml'`, `'--delete-files' action='store_true'`.
- Validación: `--delete-files` requiere `--remove-feed`.
- Orden en `main()`: tras parsear `list_feeds`, check `remove_feed` → ejecutar y return; `export/import` similar; solo si ninguna de estas operaciones se ejecuta, procede al flujo normal de descarga.

## API / Interface
- `remove_feed(conn, feed_id, delete_files=False) -> bool`
- `export_opml(db_path=None, output_path)` / `import_opml(db_path=None, input_path)`
- CLI flags como arriba.

## Testing Strategy
- Unit: `remove_feed` sin/con delete_files; export importa round-trip; import OPML con duplicados; error cases.
- Integration: crear 2 feeds, export, borrar DB, import, verificar feeds regresan.

## Out of Scope
- Actualizar URL de feed existente (se hace vía remove+re-add).
- OPML con categorías anidadas complejas — se aplanan.
