# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Project notes

- Preserve the read-only boundary: `quarterdeck.py` reads backlog, report, and `data/secondmates.md` route files directly; do not invoke fleet scripts that may refresh caches in a Firstmate home. See `docs/explanation/privacy-and-architecture.md`.
- Validation commands live in `CONTRIBUTING.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
