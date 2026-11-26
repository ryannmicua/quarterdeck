# ⚓ quarterdeck

### A local page of the things you asked Firstmate to keep.

## What it is

Quarterdeck shows saved items, including topic-based conversations, and
Catch-up notices saved by hand. The Workers page shows the Firstmate worker
roster when enabled. When you tell Firstmate "send this to Quarterdeck" or
"save this Lavish page to Quarterdeck", Firstmate runs `quarterdeck add`,
and the item appears on the page.

Each item gets an ID that is never reused: Q1, Q2, Q3 and so on. The numbered
parts of a list or a set of questions are cited as Q7.1, Q7.2, so you can say
"Q7.3 is fine, hold Q7.4" and both of you know what you mean.

## What you can save

| Kind | What it holds | Example |
| --- | --- | --- |
| `resource` | A link you want to get back to, with link text. | A Lavish page, a pull request, a preview. |
| `list` | An ordered list of entries, numbered Q7.1, Q7.2, ... | A shopping list, the steps left in a launch. |
| `questions` | Numbered questions with context, options or a suggestion, and optional [mockups](docs/how-to/save-items.md#questions-with-images). | The open questions Firstmate is waiting on, including [questions with a review link](docs/how-to/save-items.md#a-question-that-points-to-a-review-page). |

Every item saved with `add` also has a title, a one-or-two-sentence description
of what it is and why it was saved, and its saved date. Quarterdeck keeps its
own copy of the saved content.

## Features

- **Desk** puts [questions to answer](docs/how-to/answer-questions.md) and
  [unread conversation activity](docs/how-to/use-the-library.md#when-an-item-moves)
  in one table.
- **Open library** gives you searchable access to saved links, lists, and
  answered items and conversations on the served Desk. See
  [Find references and answered questions](docs/how-to/use-the-library.md).
- **Catch-up** shows notices about finished work. See
  [Catch up on finished work](docs/how-to/catch-up.md).
- **Workers** is its own page, linked from **Desk**. See
  [how to enable and check the roster](docs/how-to/read-workers.md).
- The served Desk gives each item a page with its ID and title at the top.
  A resource's link opens the saved address. List entries and questions
  are numbered.
- `quarterdeck add`, `list`, `show` and `remove` for
  Firstmate to manage items. `add` prints the new ID. See
  [Save items to Quarterdeck](docs/how-to/save-items.md).
- `quarterdeck classify` and `stats` give items a project and tags, questions a
  kind, and report how they get answered. See
  [Classify items](docs/how-to/classify-items.md) and
  [Read the report](docs/how-to/read-the-report.md).
- `quarterdeck serve` serves the page; each request reads the store, so a new
  item shows on the next load. HTTPS, a reverse-proxy path prefix and a systemd
  user service are supported. See [Serve the page](docs/how-to/serve-the-page.md)
  and the [local visit log reference](docs/reference/cli-and-config.md#visit-log).
- `quarterdeck render` writes [HTML exports](docs/reference/cli-and-config.md#pages).
- [Answer questions](docs/how-to/answer-questions.md) or
  [ask Firstmate about a question or a whole set](docs/how-to/ask-firstmate.md) or
  [talk to Firstmate about any topic](docs/how-to/talk-to-firstmate.md) in the browser.
- Python 3 standard library only.

## Quick start

```sh
curl -fsSL https://raw.githubusercontent.com/ryannmicua/quarterdeck/main/install.sh | sh
quarterdeck add --kind resource --title "Calls redesign" \
  --description "The Lavish page with the new Calls layout, to compare the two card styles." \
  --url https://example.com/lavish/calls --link-text "Calls redesign (Lavish)"
quarterdeck serve
```

`add` prints `Q1`. Open the address `serve` prints, then select
**Open library** and find **Calls redesign** under **References**. For a
read-first install, download `install.sh`, read it, then run it. Run
`quarterdeck help` for command help or `quarterdeck help --json` for the
command index.

To update an existing install, run `quarterdeck update`. See the
[tutorial](docs/tutorials/first-render.md), the [how-to guides](docs/how-to/)
and the [CLI reference](docs/reference/cli-and-config.md).

## How it relates to Firstmate

Firstmate runs the crew. Quarterdeck keeps what you ask Firstmate to save.
Firstmate can reply to a card ask or a conversation message, post a standalone
update, or open a conversation. See
[Reply from the command line](docs/how-to/reply-from-the-command-line.md).

## Privacy model

See [privacy and architecture](docs/explanation/privacy-and-architecture.md)
for where saved items live and who can see a served page. See
[Contributing](CONTRIBUTING.md) for the repository's privacy checks.

## FAQ

### What happened to the old page?

Earlier versions built the page from Firstmate's backlogs, boards, topics and
reports. There is no migration to the Desk. See the
[CLI and configuration reference](docs/reference/cli-and-config.md) for older
config keys and service flags that are still accepted.

### Does it cost tokens or contact a service?

See [when Quarterdeck uses the network](docs/explanation/privacy-and-architecture.md#network).

## License

See [LICENSE](LICENSE).
