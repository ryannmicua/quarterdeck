# Privacy and architecture

Quarterdeck is a shelf the captain fills by hand. Firstmate saves an item only
when the captain asks, with `quarterdeck add`, and the page shows those items
and nothing else.

## What Quarterdeck reads and writes

- Item commands use the [item store and its lock](../reference/item-store.md).
  `add` copies body text from the named file or standard input into the store.
- `render`, `serve`, `service` and `url` can read the
  [optional config](../reference/cli-and-config.md#json-configuration).
- `render` reads the item store and inspects existing output files before
  replacing or retiring them. See the [render reference](../reference/cli-and-config.md#pages).
- `serve` reads the item store to build pages. It reads the TLS certificate
  and key if you supply them.
- `service --write` writes the systemd unit. `url` reads it if it exists.
- `install` and `update` invoke the [user-local installer](../how-to/install.md).

It reads nothing from a Firstmate home: no backlog, board, topic, report,
ledger or saved session. On an explicit answer submission it runs only the configured home's inbox note command, passing the part ID and plain answer text.

## Why items are stored by value

An item holds its own copy of what was saved: the link, the list, the
questions with their context. A file that changes or disappears
later does not change the item, so Q7.3 means the same thing tomorrow as
today. To correct a saved item, remove it and save a new one.

## Network

See [serving security](../how-to/serve-the-page.md#security-loopback-by-default)
for the browser interface and who can reach it. Saving, rendering
and serving items make no outbound requests or model calls. The installer
uses Git to clone or update the checkout. Pages escape saved text and use
the URLs supplied with resources. They do not embed the server's config or
store paths.
