# ⚓ quarterdeck

### A local page of the things you asked Firstmate to keep.

## What it is

Quarterdeck shows only what was saved to it by hand. When you tell Firstmate
"send this to Quarterdeck" or "save this Lavish page to Quarterdeck", Firstmate
runs `quarterdeck add`, and the item appears on the page. Nothing else shows:
Quarterdeck reads no backlog, board, topic, report or session, and an empty
Quarterdeck says so.

Each item gets an ID that is never reused: Q1, Q2, Q3 and so on. The numbered
parts of a list or a set of questions are cited as Q7.1, Q7.2, so you can say
"Q7.3 is fine, hold Q7.4" and both of you know what you mean.

## What you can save

| Kind | What it holds | Example |
| --- | --- | --- |
| `resource` | A link you want to get back to, with link text. | A Lavish page, a pull request, a preview. |
| `list` | An ordered list of entries, numbered Q7.1, Q7.2, ... | A shopping list, the steps left in a launch. |
| `questions` | Numbered questions, each with its context and the options or a suggestion to decide from. | The open questions Firstmate is waiting on. |

Every item also has a title, a one-or-two-sentence description of what it is
and why it was saved, and its saved date. Its content is copied in when it is
saved; Quarterdeck never reads it again from another file.

## Features

- A Saved page: a table of items, newest first, with ID, kind, title,
  description and date. On a phone the rows stack.
- One page per item, with its ID and title at the top. A resource's link opens
  the saved address; list entries and questions are numbered.
- `quarterdeck add`, `list`, `show` and `remove` for
  Firstmate to manage items. `add` prints the new ID. See
  [Save items to Quarterdeck](docs/how-to/save-items.md).
- `quarterdeck serve` serves the page; each request reads the store, so a new
  item shows on the next load. HTTPS, a reverse-proxy path prefix and a systemd
  user service are supported. See [Serve the page](docs/how-to/serve-the-page.md).
- `quarterdeck render` writes the pages as HTML files instead.
- Read-only in the browser: no accounts and no editing on the page.
- Python 3 standard library only.

## Quick start

```sh
curl -fsSL https://raw.githubusercontent.com/ryannmicua/quarterdeck/main/install.sh | sh
quarterdeck add --kind resource --title "Calls redesign" \
  --description "The Lavish page with the new Calls layout, to compare the two card styles." \
  --url https://example.com/lavish/calls --link-text "Calls redesign (Lavish)"
quarterdeck serve
```

`add` prints `Q1`. Open the address `serve` prints. For a read-first install,
download `install.sh`, read it, then run it. Run `quarterdeck help` for command
help or `quarterdeck help --json` for the command index.

To update an existing install, run `quarterdeck update`. See the
[tutorial](docs/tutorials/first-render.md), the [how-to guides](docs/how-to/)
and the [CLI reference](docs/reference/cli-and-config.md).

## How it relates to Firstmate

Firstmate runs the crew. Quarterdeck is a separate shelf: Firstmate puts
things on it only when you ask, with the `quarterdeck` command. Quarterdeck
never reads a Firstmate home and never writes to one.

## Privacy model

Saved items live in one file Quarterdeck owns,
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/items.json` (mode 0600). The
pages contain the items you saved and nothing else; they name no server path.
`serve` binds 127.0.0.1 by default; a non-loopback address exposes your saved
items to that network with no login. See
[privacy and architecture](docs/explanation/privacy-and-architecture.md).

The repo ignores common output folders and local config files. Run
`python3 scripts/privacy_guard.py --staged` before committing; it rejects
generated pages, host-specific paths, private IPs and pull-request links or
ticket-shaped keys in staged content. Examples and tests use invented data.

## FAQ

### What happened to the old page?

Earlier versions built the page from Firstmate's backlogs, boards, topics and
reports. That content no longer shows: there is no migration, and the code
that read it is gone. An existing config that lists `homes` still loads; the
key is ignored.

### Does it cost tokens or contact a service?

No. Quarterdeck does not call a model and makes no network calls of its own.

## License

See [LICENSE](LICENSE).
