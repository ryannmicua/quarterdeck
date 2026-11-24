# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Project notes

- Preserve the boundary: Quarterdeck shows only items saved by hand with `quarterdeck add`. It reads and writes only its own item store (`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/items.json`), its optional config, the body file named on `add`/`edit`, and its `render` output and `service --write` unit. It reads nothing from a Firstmate home, runs no Firstmate script, and makes no network calls; `serve` is read-only (POST is refused) and binds loopback by default. See `docs/explanation/privacy-and-architecture.md`.
- Validation commands live in `CONTRIBUTING.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
