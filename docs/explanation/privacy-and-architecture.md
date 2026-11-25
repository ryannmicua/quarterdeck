# Privacy and architecture

Quarterdeck is a shelf the captain fills by hand. Firstmate saves an item only
when the captain asks, with `quarterdeck add`. The page shows those items and,
when enabled, the live Firstmate workers roster.

## What Quarterdeck reads and writes

- Item commands use the [item store and its lock](../reference/item-store.md).
  `add` copies body text from the named file or standard input into the store.
  A questions body can also name [local image attachments](../reference/item-store.md#question-images).
- `render`, `serve`, `service` and `url` can read the
  [optional config](../reference/cli-and-config.md#json-configuration).
- `render` reads the item store and saved images. It inspects existing output
  files before replacing or retiring them. See the [render reference](../reference/cli-and-config.md#pages).
- `serve` reads the item store to build pages and writes to it when users
  submit answers, send asks or acknowledge replies to an opened question.
  Page reads leave the store intact. It serves question and option images
  from `images` and reply images from `reply-images`, using the files
  listed for that item in the store. It reads the TLS certificate and key
  if you supply them.
- The [workers endpoint](../reference/cli-and-config.md#workers-endpoint) runs
  the configured home's read-only `bin/fm-fleet-snapshot.sh --local`. This
  command uses no model.
- `service --write` writes the systemd unit. `url` reads it if it exists.
- `install` and `update` invoke the [user-local installer](../how-to/install.md).

Apart from explicitly supplied `add` inputs and the workers snapshot,
Quarterdeck reads no backlog, board, topic, report, ledger or saved session
from a Firstmate home, and it does not read terminal text. Quarterdeck stores
no snapshot data or contact history on the server.
Quarterdeck writes nothing to the home except through the configured home's
`bin/fm-inbox.sh note` command on explicit answer or ask submission. It passes
an answer as the part ID followed by the text. Asks go to the explicit
`firstmate_home` with `ASK`, the item or question ID and the ask ID before the text.
Replies come back only through `quarterdeck reply`, which Firstmate runs on
the same machine. Quarterdeck never calls a model.

Firstmate handles any periodic checks for decisions made in chat or on review
pages. Quarterdeck records the [source supplied with `mark`](../how-to/read-answers.md)
and builds its [report](../how-to/read-the-report.md) from the local item store.

## Why items are stored by value

An item holds its own copy of what was saved: the link, the list, the
questions with their context. A file that changes or disappears
later does not change the item, so Q7.3 means the same thing tomorrow as
today. To correct the saved content, remove the item and save a new one.
To change its classification, use [`classify`](../how-to/classify-items.md#change-it-later).

## Network

See [serving security](../how-to/serve-the-page.md#security-loopback-by-default)
for the browser interface and who can reach it. Saving, rendering
and serving pages make no outbound requests or model calls. The installer
uses Git to clone or update the checkout.

The served Desk includes archived question and answer text in its HTML so the
Answered tab can search it in the browser.

Opening a link takes the browser to the saved destination. Pages do not embed
the server's config or store paths. See [inline web addresses](../reference/item-store.md#inline-web-addresses)
for how pages escape saved text and display links.
