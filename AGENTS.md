# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Project notes

- Preserve the boundary: Quarterdeck shows only items saved by hand with `quarterdeck add`, plus the workers section, which runs only the home's read-only `bin/fm-fleet-snapshot.sh --local`. It ingests nothing else from a Firstmate home and invokes only the configured inbox note command on answer submission. See `docs/explanation/privacy-and-architecture.md` for the file access and serving boundaries.
- Validation commands live in `CONTRIBUTING.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
