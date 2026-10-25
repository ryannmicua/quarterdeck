# Keep the captain board current

The board only helps when it matches what the captain is working on. Hand-edited
JSON went stale, so Quarterdeck ships a small writer that Firstmate calls on
each change. `upsert`, `touch`, and `remove` take the backlog task id;
`active` records operator messages; `list` reads the board. See the [CLI reference](../reference/cli-and-config.md)
for command syntax and home selection.

## When the captain sends a message

Run this first, before any `touch` or `upsert` for that turn, each time the
captain sends Firstmate a message or comments on a watched brief:

```sh
quarterdeck board active
```

It records the current message time as `operator.last_active_at`. Pass
`--new-session` when a new Firstmate chat starts. This records session context;
item engagement is recorded separately with `touch` or `upsert --touch`. See
the [board file reference](../reference/captain-board.md#what-the-page-does-with-it)
for shared relevance and the fallback session rules.

So that the desk shows every question Firstmate asks in a session, put each
ask on the board in the same turn with `upsert ... --touch`, and `touch` items
the captain picks from `/bearings` so they return to the desk.

## When something starts waiting on the captain

```sh
quarterdeck board upsert dock-fix --group merge --title "Dock merge" \
  --ask "Merge the dock patch? Checks are green." --project dock-demo \
  --brief-url https://example.com/brief/dock --recommendation "Merge"
```

A new item needs `--group`, `--title` and `--ask`. Its id defaults to the task
id with characters outside letters, digits, underscores, dots and hyphens
replaced by hyphens, then leading and trailing hyphens removed.
Commands match the task field only; use explicit `--id` with `upsert` or
`remove` to select a board item id. `touch` updates every item for the task.
Running `upsert` again updates only the options you pass, so this adds a brief
later:

```sh
quarterdeck board upsert dock-fix --brief-url https://example.com/brief/dock --recommendation "Merge"
```

An empty value (`--topic ""`) clears `--detail`, `--topic`, `--project`,
`--brief-url` or `--recommendation`. `--link LABEL=URL` (repeatable) replaces
the item's links.

`upsert` leaves `touched_at` alone unless you pass `--touch` (now) or
`--touched-at ISO`. An untouched ask can still qualify through recorded feed
or task activity under the shared relevance rules. See the
[board file reference](../reference/captain-board.md#what-the-page-does-with-it).
Pass `--touch` when Firstmate raises the ask in its conversation with the
captain, so that engagement is recorded for relevance.

## When the captain engages with it

Run this whenever the captain discusses, answers, comments on, or asks about
the item or its project:

```sh
quarterdeck board touch dock-fix
```

It sets `touched_at` to now on every item that names the task (or `--at ISO`
for an earlier engagement; a newer recorded time is kept). It exits 1 when no
item names the task, so a missing board entry is noticed instead of silently
skipped; add it with `upsert`.

Touching is the signal that matters most. Only Firstmate, in the
conversation, can tell which item the captain means when they name work in
plain language, so it records that judgment here rather than leaving
Quarterdeck to guess.

## When it stops waiting

```sh
quarterdeck board remove dock-fix
```

Removing a task with no item succeeds. Settled items also disappear from the
page on their own once the backlog shows them closed, so a missed remove is
harmless.

## Check the board

```sh
quarterdeck board list          # task, id, group, touched_at, title per line
quarterdeck board list --json   # the raw file
```

## Safety

- Writes are atomic (a temporary file in the same directory, then rename) and
  serialised by a lock on the data directory, so concurrent calls do not lose
  each other's changes and no lock file is left in the home.
- The writer refuses to overwrite a board that is not valid JSON, not
  version 1, or has no items list, and refuses a symbolic-link board file.
- It validates every value before writing (group name, http(s) URLs, ISO
  times) and keeps keys it does not know, so newer fields survive.
- `render` and `serve` never call it; they stay read-only.

See the [board file reference](../reference/captain-board.md) for the schema
and ranking rule.
