# AGENTS.md — RSS Podcast Downloader

Conventions for any agent (DSH / DeepSeek Harness, opencode, etc.) working in this repo.
This file bridges the author's personal opencode skillset into agent sessions.

## Reusable skill store (source of truth)

The author maintains skills + memory for opencode, stowed at:

- Skills: `~/.config/opencode/skills/<name>/SKILL.md`
  (resolves via `~/.dotfiles/stow/opencode/.config/opencode/skills/...`)
- Global workflow / sizing: `~/.config/opencode/AGENTS.md`
- Session memory: `~/.config/opencode/memory/*.md`

These are plain Markdown. When a task matches, READ the relevant file and treat
its contents as authoritative instructions for that task. Do not assume their
contents from the summary below.

## Skill → when to load (per task)

| Skill file | Load when working on... |
|------------|--------------------------|
| `sdd` | Any non-trivial / new feature — write a spec to `docs/specs/<name>.md` before coding |
| `testing` | Adding/structuring Python tests (uses `tests/unit` + `tests/integration`, pytest) |
| `automation` | CI/CD, Makefiles, script automation |
| `docs` | README / ARCHITECTURE.md / project documentation templates |
| `adversarial-review` | Before merging, or after any M/L change — run the hunt checklist |
| `docker` / `django` / `fastapi` / `nvim` / `zellij` | Matching technology work |

> Note: this agent's own session skill catalog is fixed at startup and does NOT
> include these files. Load them by reading the path above when relevant.

## Right-sizing workflow (from author's global AGENTS.md)

Classify each task FIRST, then run only the phases its size requires.

- **S (small)** — single file, no logic change (typo, comment, config value,
  one string). Implement only. No spec/review/tests unless the diff changes logic.
- **M (medium)** — multiple files OR new logic / branching / thresholds.
  Phases: understand → implement → test → adversarial review. Write a spec
  (`sdd`, save to `docs/specs/`); dispatch an adversarial review on the diff.
- **L (large)** — architecture / data-model / security / cross-cutting refactor,
  or the user says "plan/spec first". Full pipeline: understand → implement →
  test → adversarial review → verify → deploy, with explicit user approval gates
  at plan and before deploy.

When unsure, round UP one size.

## Session memory

Read `~/.config/opencode/memory/` only when a task directly relates to the
author's infrastructure, configuration, or previously persisted work
(e.g. workstation, home lab, deployment targets). Do not load proactively on
every task.

## Project specifics (RSS Podcast Downloader)

- Single-file Python CLI: `rss-podcast-downloader.py` (requests, feedparser, mutagen, sqlite3).
- State persisted in `downloads.db` (tables `feeds`, `episodes`).
- Env: local `.venv` (Python 3.14). Current working branch `feature/stateful-download-tracking`.
- Current code base has NO tests, NO packaging, NO type hints. Known issues
  documented in conversation analysis (unused `content` param, `--num-episodes`
  ordering assumption, limited date-format coverage, whole-file buffered downloads).
- This repo is a standalone-script project: per `sdd`, code is regenerable from a
  spec — do not hesitate to restructure.
