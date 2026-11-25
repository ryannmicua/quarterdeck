# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Project notes

- Quarterdeck items are saved by hand with `quarterdeck add`. With Firstmate configured, anyone who can reach the served Desk can also start a conversation. The workers section runs only the home's read-only `bin/fm-fleet-snapshot.sh --local`. See `docs/explanation/privacy-and-architecture.md` for the file access and serving boundaries.
- Validation commands live in `CONTRIBUTING.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
