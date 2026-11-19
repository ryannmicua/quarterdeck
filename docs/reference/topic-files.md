# Topic files

A topic file holds one conversation between the owner and Firstmate about one piece of work, such as a launch or a
renewal. Firstmate writes it; Quarterdeck only reads it and shows it as a thread page. This page is the contract
between the two. [`examples/maple-harbor-catalog.md`](examples/maple-harbor-catalog.md) is a complete example, and
`quarterdeck topics check` reports every mistake.

## Where the files live

Each topic is one Markdown file in the home's data directory:

```text
<home>/data/topics/<topic-id>.md
```

- The file name is the topic id: lower-case letters, digits and dashes, starting with a letter or digit, at most
  64 characters. `maple-harbor-catalog.md` has the id `maple-harbor-catalog`.
- Every `.md` file in `data/topics/` is a topic. Names that start with `_` or `.` are skipped, so `_draft.md` stays
  private until it is renamed.
- A file larger than 2 MB is not shown. Start a new topic and link the old one.
- A topic is a work area, not one backlog task. One topic usually groups several tasks; list them under `Pieces`.

## Layout

A file has two parts: the head (title and state), then the entries, oldest first. Add new entries at the end and
never rewrite old ones. To correct something, add an entry that says so.

```markdown
# Maple Harbor catalog launch

Now: The catalog import works; PR 42 is ready to merge.
Waiting on: you - merge PR 42
Waiting on: Sample vendor - a storage quota for the product images
Pieces: maple-21, paper-27, blocked-33
Links: [Catalog preview](https://example.com/maple-harbor/preview), data/amber-18/report.md
Status: open

## 2026-03-02 08:12 | you | chat
Get the Maple Harbor catalog live before the spring sale.

## 2026-03-02 08:20 | firstmate | chat
Started. I split it into three pieces.
```

## The head

The first line is the title: `# ` followed by the topic's name. The state lines follow, each `Name: value` on one
line, in any order. Change them in place as the work moves; they always describe the topic as it stands now.

| Line | Repeat | What it holds | How it shows |
| --- | --- | --- | --- |
| `# Title` | no | The topic's name. Required. | The page heading and the front-page row. |
| `Now:` | no | One or two sentences on where the work stands. Several `Now:` lines are joined. | First row of the state. |
| `Waiting on:` | yes | `who - what`, one line per wait. `who` is `you` for the owner, or any name. The dash may be `-`, `–` or `—` with spaces around it; with no dash the whole line is who. | Waits on `you` show in the signal colour, and each counts under **Needs you** on the front page while the topic is open. |
| `Pieces:` | yes | Backlog task ids, separated by commas or spaces. | Each task's title, state and item page link from the backlog; a recent unreviewed report of a piece shows under **To review**. |
| `Links:` | yes | `[label](https://...)`, bare `https://` addresses, `[label](data/<id>/<file>)` or `data/<id>/<file>.md` / `.html` paths, separated by commas. | Links; a `data/` document opens as a readable page. Report paths use the [shared report view](../how-to/watch-activity-cards.md#read-the-report). A discovered report named through either `report.md` or `report.html` that is unreviewed and under two weeks old also shows under **To review**. |
| `Status:` | no | `open` (the default) or `done`. | A done topic leaves the front page and is found in search. |

Blank lines and single-line `<!-- comments -->` in the head are ignored. Any other line is a mistake and is not shown.

## Entries

Each entry starts with a heading line and holds one message:

```markdown
## <time> | <who> | <where>
```

- **time**: `YYYY-MM-DD HH:MM`, with optional seconds. `T` may replace the space. Add `Z`, `UTC` or an offset such
  as `+08:00`; a time with no zone is read as UTC. Pages show times in the reader's own time zone.
- **who**: who said it, not who wrote it down. `you` (or `owner`, `captain`) is the owner; `firstmate` (or
  `first mate`) is Firstmate; anything else, such as `IT Ops`, is shown as written.
- **where**: `chat` for something said in the Firstmate chat, `quarterdeck` (or `here`) for something sent from
  Quarterdeck.
- `·` may separate the parts instead of `|`.

The lines after the heading are the message, in Markdown. Quarterdeck escapes any HTML in it, and links only `http`,
`https` and `mailto` addresses. A `## ` line inside a ``` code block is part of the message, not a new entry.

### Fields at the top of an entry

An entry may start with field lines, before its message. Field names are not case-sensitive.

| Field | Value | Meaning |
| --- | --- | --- |
| `Id:` | At most 64 letters, digits, dots, dashes or underscores, starting with a letter or digit, unique in the file. | Lets later entries name this one. |
| `Re:` | One or more ids, separated by commas. | This entry answers or replies to those entries. |
| `Decision:` | The question, written to be answered. | Makes the entry a decision card. Needs an `Id`. |
| `Options:` | Answers separated by `\|`, such as `Merge \| Hold \| Not now`. | The choices. Leave it out when the answer needs words. |
| `Recommend:` | One of the options. | Firstmate's recommendation. |

A `Re:` line whose value is not a list of ids, such as `Re: the certificate, I sent it`, is part of the message.

### Decisions

A decision is an entry with `Decision:` and `Id:`. Put the context the owner needs in its message. Write one
decision per question: when one piece of work needs three answers, write three decision entries.

A decision stays open until a later entry names its id in `Re:`. Record the owner's answer as an entry from `you`,
even when it was given in chat. When a decision no longer matters, close it with a `firstmate` entry whose `Re:`
names it and says why.

```markdown
## 2026-03-04 09:00 | firstmate | chat
Id: images-host
Decision: Wait for the vendor's quota, or host the images ourselves for now?
Options: Wait | Host ourselves
Recommend: Wait

Hosting them ourselves costs a day of work that we throw away once the quota arrives.

## 2026-03-04 09:40 | you | chat
Re: images-host
Wait. Ask the vendor again on Friday.
```

Decisions still awaiting the owner's answer are pinned above the conversation and counted on the front page.
Once its answer is recorded in the topic file, a decision stays in the conversation with the first line of its answer.
For cards answered from Quarterdeck before the file records them, see
[Answer a decision](../how-to/keep-topic-threads.md#answer-a-decision-owner-served-page).

### Questions sent from Quarterdeck

A question the owner sends from Quarterdeck carries an id of `ask-` and 16 letters and digits, such as
`ask-3f9c2a1b0d4e5f60`; Firstmate's inbox note names it and the topic. Copy the question into the file as an entry
from `you` at `quarterdeck` with that `Id:`, and answer it with `Re:` naming the same id. A `Re:` that names an
`ask-` id is allowed even when the question itself is not in the file. Until the file shows the question, the
thread page shows it at the end of the conversation as waiting; the answer appears once a `Re:` names it. A
question sent from the front page as **New topic** goes to the first configured home; start or pick a topic for it.

### Answers sent from Quarterdeck

An answer the owner gives on a decision card, a tapped option or words typed under **Answer in words**, reaches
Firstmate as an inbox note naming the topic and the decision, with the whole answer. Record it as an entry from `you`
at `quarterdeck` with `Re:` naming the decision. Quarterdeck keeps its own record of delivered answers; failed
deliveries are not recorded. Quarterdeck refuses an answer to a decision the file already shows answered, and a
second answer to a decision already answered from Quarterdeck. The
[answering guide](../how-to/keep-topic-threads.md#answer-a-decision-owner-served-page) describes the card's display,
counts and persistence until the file records the answer.

## Mistakes

For a file with a valid topic id, Quarterdeck shows what it can read and lists each mistake at the bottom of the
thread page. An unreadable file or one over 2 MB gets an error-only thread; an invalid topic id gets no thread page.
`quarterdeck topics check` reports these errors too and prints the same problem list:

```text
data/topics/maple-harbor-catalog.md:14: error: an entry heading starts with its time, like `## 2026-03-04 08:12 | firstmate | chat`; 'soon' is not a time
data/topics/maple-harbor-catalog.md: warning: Pieces names maple-99, which is not in the backlog
1 topic file checked: 1 error, 1 warning.
```

See the [CLI reference](cli-and-config.md) for checker exit statuses, including incomplete checks.

| Level | Mistakes |
| --- | --- |
| error | A missing `# Title` line; a file name that is not a topic id; an entry heading without a readable time or without who; a `Decision` without an `Id`; an empty `Decision`; an `Id` used twice; a `Re:` naming an id no earlier entry has; a file that cannot be read or is over 2 MB. |
| warning | A head line that is not a known field; a misspelled field such as `Waiting for` or `Recommended`, named with the spelling to use; an empty field; a piece that is not a task id or not in the backlog; a link that is not http(s) or `data/`; an unknown `Status`; a missing or unknown where; extra heading parts; an entry with no text; an entry dated before the one above it; `Options` or `Recommend` without a `Decision`; a decision with one option; a recommendation that is not an option; an unclosed code block. |

Pieces are checked against the backlog when the home is known: with `--home`, with `FM_HOME` or the configured homes
when no file is named, or when a named file sits in `<home>/data/topics/` beside `<home>/data/backlog.md`.
