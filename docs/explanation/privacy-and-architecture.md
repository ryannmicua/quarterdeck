# Privacy and architecture

Quarterdeck is a shelf the captain fills by hand. Firstmate saves an item only
when the captain asks, with `quarterdeck add`. The page shows those items and,
when enabled, the live Firstmate workers roster.

## What Quarterdeck reads and writes

- Item commands use the [item store and its lock](../reference/item-store.md).
  `add` copies body text from the named file or standard input into the store.
  When a questions body names image files, `add` reads each one once, copies
  it into the store's private `images` folder and never reads that path again.
- `render`, `serve`, `service` and `url` can read the
  [optional config](../reference/cli-and-config.md#json-configuration).
- `render` reads the item store and inspects existing output files before
  replacing or retiring them. See the [render reference](../reference/cli-and-config.md#pages).
- `serve` reads the item store to build pages and writes to it when users
  submit answers. It serves an image only from the private `images` folder and
  only when the store lists it for that item. It reads the TLS certificate and key if you supply them.
- The [workers endpoint](../reference/cli-and-config.md#workers-endpoint) runs
  the configured home's read-only `bin/fm-fleet-snapshot.sh --local`. This
  command uses no model.
- `service --write` writes the systemd unit. `url` reads it if it exists.
- `install` and `update` invoke the [user-local installer](../how-to/install.md).

Apart from that snapshot, Quarterdeck reads no backlog, board, topic, report,
ledger or saved session from a Firstmate home, and it does not read terminal
text. Quarterdeck stores no snapshot data or contact history on the server.
Quarterdeck writes nothing to the home except through the configured home's
`bin/fm-inbox.sh note` command on explicit answer submission. It passes the
plain answer text prefixed with the part ID.

## Why items are stored by value

An item holds its own copy of what was saved: the link, the list, the
questions with their context. A file that changes or disappears
later does not change the item, so Q7.3 means the same thing tomorrow as
today. To correct a saved item, remove it and save a new one.

## Network

See [serving security](../how-to/serve-the-page.md#security-loopback-by-default)
for the browser interface and who can reach it. Saving, rendering
and serving items make no outbound requests or model calls. The installer
uses Git to clone or update the checkout.

Opening a link takes the browser to the saved destination. Pages do not embed
the server's config or store paths. See [inline web addresses](../reference/item-store.md#inline-web-addresses)
for how pages escape saved text and display links.
