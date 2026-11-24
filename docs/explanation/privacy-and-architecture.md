# Privacy and architecture

Quarterdeck is a shelf the captain fills by hand. Firstmate saves an item only
when the captain asks, with `quarterdeck add`, and the page shows those items
and nothing else.

## What Quarterdeck reads and writes

- It reads and writes one file it owns, the item store at
  `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/items.json` (mode 0600, in a
  0700 directory). See [Item store](../reference/item-store.md).
- It reads its optional config, `${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json`.
- `render` writes the pages to its output directory; `service --write` writes
  the systemd unit.
- `add` reads the body file you name, once, and copy its text into
  the store.

It reads nothing from a Firstmate home: no backlog, board, topic, report,
ledger or saved session. It runs no Firstmate script and writes nothing into a
Firstmate home.

## Why items are stored by value

An item holds its own copy of what was saved: the link, the list, the
questions with their context. A file that changes or disappears
later does not change the item, so Q7.3 means the same thing tomorrow as
today. To correct a saved item, remove it and save a new one.

## IDs

Each item gets the next number from `next_id`, which only grows. Removing Q7
never frees Q7 for another item, so an old reference cannot point to new
content.

## Network

`serve` binds 127.0.0.1 by default and has no login. A server bound only to
loopback refuses requests whose `Host` is not loopback. The page is read-only:
`POST` is refused. Quarterdeck makes no outbound network calls and does not
call a model. Pages escape all saved text, link only the `http` and `https`
addresses saved as resources, and name no server path.
