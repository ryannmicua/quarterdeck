# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Project notes

- With Firstmate configured, anyone who can reach the served Desk can start a conversation. The workers section runs only the home's read-only `bin/fm-fleet-snapshot.sh --local`. See `docs/explanation/privacy-and-architecture.md` for the file access and serving boundaries.
- Validation commands live in `CONTRIBUTING.md`.
- On a wide screen the Desk shows an item page in a same-origin frame beside its lists. Item pages that take the owner's actions therefore send `frame-ancestors 'self'`, and the Desk itself sends `'none'`. The framed page reports each saved change to the Desk, which then reads its lists again from `index.html?lists=1`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
