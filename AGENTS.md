# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Project notes

- Preserve the read-only boundary: `quarterdeck.py` reads backlog, report, `data/secondmates.md` route, `data/qd-adoption/tags.jsonl`, and `state/fleet-ledger.jsonl` files directly, and the captain-wait watcher also reads the home's saved Claude Code session files. The only scripts it may run in a home are `bin/fm-bearings-snapshot.sh --json` and `bin/fm-attention.sh --json` (both skippable with `--no-snapshot` for the page; `quarterdeck attention` always runs the latter) and, for a reaction sent from `serve --allow-marks` or `quarterdeck reactions import` or a captain-wait discard, `bin/fm-inbox.sh note`; invoke no other fleet scripts. The only home write is the explicit `quarterdeck board` command, limited to `data/captain-board.json`. `serve` binds loopback by default and enables its report-marking, reaction, saved-view and wait-discard writes only with `--allow-marks`. See `docs/explanation/privacy-and-architecture.md`.
- Validation commands live in `CONTRIBUTING.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
