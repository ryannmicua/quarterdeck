# Privacy and architecture

Quarterdeck keeps a local copy of deliberately saved items. See
[serving security](../how-to/serve-the-page.md#security-loopback-by-default)
for who can start conversations and use the other browser controls.

## What Quarterdeck reads and writes

- Item commands use the [item store and its lock](../reference/item-store.md).
  `add` copies body text from the named file or standard input into the store.
  A questions body can also name [local image attachments](../reference/item-store.md#question-images).
  `quarterdeck reply --image` reads the explicitly named image files and copies
  them into the [private reply image folder](../reference/item-store.md#store-layout).
- `render`, `serve`, `service` and `url` can read the
  [optional config](../reference/cli-and-config.md#json-configuration).
- `render` reads the item store and the images attached to questions and
  options. It inspects existing output files before replacing or retiring
  them. See the [render reference](../reference/cli-and-config.md#pages) for
  which items are exported.
- `serve` reads the item store to build pages and writes to it when users
  submit answers, send asks, start conversations, send messages or acknowledge
  replies. It also writes the [local visit log](../reference/cli-and-config.md#visit-log).
  It serves only files listed for the requested item from
  the corresponding [private image folder](../reference/item-store.md#store-layout).
  It reads the TLS certificate and key if you supply them.
- The [workers endpoint](../reference/cli-and-config.md#workers-endpoint) runs
  the configured home's read-only `bin/fm-fleet-snapshot.sh --local` when the
  Workers page opens and no fresh saved reading is available. Anyone who can reach the
  service can also trigger a fleet read with **Check workers** or a direct
  `GET /workers.json`. This command uses no model.
- `service --write` writes the systemd unit. `url` reads it if it exists.
- `install` and `update` invoke the [user-local installer](../how-to/install.md).

Apart from explicitly supplied `add` inputs, images named with `reply --image`,
and the workers snapshot,
Quarterdeck reads no backlog, board, topic, report, ledger or saved session
from a Firstmate home, and it does not read terminal text. Quarterdeck holds
the last workers reading in server memory only, so it is lost when `serve`
stops and is never written to disk. Loading the Desk never runs the snapshot. It saves card asks, conversation
messages and replies in the [item store](../reference/item-store.md#conversations).
Quarterdeck writes nothing to the home except through the configured home's
`bin/fm-inbox.sh note` command when a user sends or retries an answer,
[card ask](../reference/cli-and-config.md#ask-endpoint), or
[conversation message](../reference/cli-and-config.md#conversation-endpoint).
It passes an answer as the part ID followed by the text.
Replies come back only through `quarterdeck reply`, which Firstmate runs on
the same machine. Quarterdeck never calls a model.

Firstmate handles any periodic checks for decisions made in chat or on review
pages. Quarterdeck records the [source supplied with `mark`](../how-to/read-answers.md)
and builds its [report](../how-to/read-the-report.md) from the local item store.

## Why items are stored by value

An item holds its own copy of what was saved: the link, the list, the
questions with their context. A file that changes or disappears
later does not change the item, so Q7.3 means the same thing tomorrow as
today. To correct content you saved with `add`, remove the item and save a new one.
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
