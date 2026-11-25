# Item store

Quarterdeck keeps every saved item in one JSON file it owns:

```text
${XDG_STATE_HOME:-~/.local/state}/quarterdeck/items.json
```

Store writers, including item commands and browser submission handlers, set
the directory to mode 0700, take an exclusive lock on `items.json.lock`, validate
the existing records, and atomically replace `items.json` with mode 0600.
An in-place `render` takes the same exclusive lock from its store read
through publication. An external render takes a shared lock if it can
open the lock file and otherwise reads without coordinating with writers.
`stats` uses the same shared-lock rule for its store read. Both can read from
a read-only store. Other readers validate records without taking the lock.

## Format (version 1)

```json
{
  "version": 1,
  "next_id": 4,
  "items": [
    {
      "id": "Q3",
      "kind": "questions",
      "title": "Tide launch decisions",
      "description": "The question holding the tide launch.",
      "saved_at": "2026-11-24T09:30:00Z",
      "body": {"questions": [
        {"question": "Merge the tide tables now?", "context": "Checks are green.",
         "options": ["Merge", "Hold"], "suggestion": "Merge"}
      ]}
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `next_id` | The number the next item gets. It only grows, so a removed item's ID is never given out again. |
| `id` | Uppercase `Q` followed by a positive integer without leading zeros. Parts are cited as `Q3.1`, `Q3.2` in the order they are stored. |
| `kind` | `resource`, `list`, `questions` or `conversation`. It does not change. |
| `title` | Up to 200 characters after whitespace is collapsed to single spaces. |
| `description` | Up to 600 characters describing what the item is and why it was saved. |
| `saved_at` | UTC time when the item was saved. |
| `project` | Optional. A plain project name or an `owner/name` repository, up to 40 characters after whitespace is collapsed. Name parts may contain letters, digits, internal spaces, dots, hyphens, and underscores. A qualified name uses one slash, with a nonempty name on each side. In a qualified name, each part must differ from `.` and `..`. |
| `tags` | Optional list of up to three distinct [classification tags](cli-and-config.md#classification). Repeated tags collapse to one. An empty list is omitted. |
| `body` | Depends on the kind; see below. |

Served pages and rendered HTML display saved dates in the browser's local
time zone when JavaScript is enabled.

| Kind | `body` |
| --- | --- |
| `resource` | A `url` and `link_text`. |
| `list` | `{"entries": ["...", "..."]}`, at least one. |
| `questions` | A nonempty `questions` array of objects. `question` and `context` are required text fields. `options` can be empty when the object has a `suggestion`. Each question may include `links`, an array of objects with exactly two fields, `text` and `url`. See [question images](#question-images) for option and attachment fields. |

A question may also carry `kind` (one word) and its own `tags`, which follow
the same list rules as item tags. An unclassified question omits `kind`.

Resource and question link URLs must use `http` or `https`, include a hostname,
and pass port validation. After trimming, a URL may contain at most 2,000
characters and cannot contain whitespace or backslashes.
Question link `text` must be nonempty and is limited to 200 characters after
whitespace is collapsed to single spaces. Quarterdeck omits an empty `links`
array when saving.

## Classification

Kinds and tags are lowercase words. Project names preserve case. The store
checks that existing values are well formed. `add`, `classify` and `mark`
check new values against the [fixed lists](cli-and-config.md#classification).
Items saved before these fields existed need no migration.

## Inline web addresses

Quarterdeck trims text when saving it and escapes it when building pages.
Standalone `http://` and `https://` addresses become links in the following
fields if they pass the same validation as resource URLs.

| Where | Fields |
| --- | --- |
| Desk and References | Item descriptions and open question prompts on Desk cards, plus item descriptions in References on served and rendered pages |
| Item page | Item descriptions, question prompts, context, options and suggested answers |
| Served item page | Ask and reply text in whole-item and numbered-question conversations |

Quarterdeck uses the address as the link text. Inline links open in a new tab
so you can keep a draft answer open while following a link. Selecting an option
or using a suggestion still fills the answer with its plain text.

Separate multiple addresses with spaces so they are recognized individually.
Bare addresses lose trailing punctuation such as periods and exclamation
marks. Enclose an address in angle brackets, as in
`<https://example.com/?q=go!>`, to keep that punctuation in the destination.

## Question images

A question's optional `images` field is an array of attachments. An option
may be an object with a `label` and a single `image` attachment. Plain text
options are strings. After whitespace normalization, a pictured option's label
must be distinct from all other option labels in its question.

See the [example questions body](../how-to/save-items.md#questions-with-images).

| Field | Meaning |
| --- | --- |
| `path` | A path of up to 4,000 characters to a local regular file containing a PNG, JPEG or still WebP image, up to 5 MB (5,000,000 bytes). Quarterdeck checks the file contents to identify its type. It rejects directories, named pipes and other nonregular files. |
| `alt` | Required text that describes the image, up to 300 characters. |

Relative paths are resolved from the directory where you run `quarterdeck add`.
Paths beginning with `~/` are expanded from your home directory.

An item can name up to 24 images across questions and options. `add` validates
the whole body before opening any attachment. It resolves each distinct path
spelling once per save and reads each resolved file once. Two spellings can
resolve to different targets if a symlink is retargeted during the save.
Quarterdeck uses the standard library's resolved path even when the operating
system would reject the original spelling, such as a parent step through a
regular file. Different paths to a hard-linked file count as separate reads.

Quarterdeck copies the original bytes into the store. The original can move
or go away after saving. The stored image entry replaces `path` with `file`:

```json
{"file": "3f9a0c2d41b7e6a8.png", "alt": "A card with a frame"}
```

`file` is the first 16 hexadecimal characters of the content's `sha256` digest
plus the type's extension.

### Store layout

```text
${XDG_STATE_HOME:-~/.local/state}/quarterdeck/images/<id>/<file>
${XDG_STATE_HOME:-~/.local/state}/quarterdeck/reply-images/<id>/<file>
```

Both image folders have mode 0700. Newly written image files have mode
0600. `<id>` is the lowercase item ID, such as `q3`. `images` holds
question and option images for live pages and exports. `reply-images`
holds conversation images for live pages only. `remove` deletes files named
in the removed item's record from their respective folders and leaves the
item folders in place. `add` and `render` preserve saved image files.

See the [supported umask](../how-to/save-items.md#known-limits).

A failed `add` or `reply`, or an interrupted `remove`, can leave files that no item names.
Retrying does not clean them up. Where the destination does not support hard
links, an interrupted image write during `add`, `reply`, or `render` can leave a partial
file. Delete that partial file by hand before retrying the write. See the
[serving boundary](../explanation/privacy-and-architecture.md#what-quarterdeck-reads-and-writes)
and [export behavior](cli-and-config.md#rendered-images).

## Conversations

A questions item may contain a `conversation` object with `item` for the
whole-item thread and one-based part numbers as string keys for threads
on individual questions. These threads are separate from `answers`.

A standalone `conversation` item requires a `conversation` object with only
the key `item`. Its `body` is `{}` and its `description` is a generic label.
Older descriptions are accepted and omitted from listings. The home binding
on the first message also binds the conversation. See
[how to start a conversation](../how-to/talk-to-firstmate.md).

Each thread stores messages in the order they were saved. Every reply
names the earlier ask or message it answers.

Every message has `id` (a string of 32 hexadecimal characters), `author`, `text` (plain
text, up to 20,000 characters) and `at` (UTC).

| `author` | Other fields |
| --- | --- |
| `you` | An ask or conversation message with a browser-generated ID. `delivery` (`pending` or `sent`), a stable `request_id` string for inbox retries, and `home`, a hexadecimal `sha256` digest of the resolved path of the configured Firstmate home. |
| `firstmate` | A reply added by `quarterdeck reply`. `ask` (ID of an earlier message from `you` in the same thread), `read` (true once the page acknowledges this reply), and optional `links` (the same `text` and `url` objects as question links) and `images` (`file` and `alt`). |

Reply images use the item's [private reply image folder](#store-layout).

## Question states

The stored `state` is Open, Answered or Closed. The served page derives
**Replied** from unread replies without changing the stored state. See
[how to read replies](../how-to/ask-firstmate.md) and
[what static exports include](cli-and-config.md#pages).

## Question answers

Questions items may contain an `answers` object keyed by the one-based part number as a string. Missing entries mean Open. Records carry `state` (open, answered or closed) and an opaque `revision` for detecting concurrent edits. Older items need no migration.

Each answer adds an entry to `answer_marks`, in recording order. An entry
contains `via` and `answered_at` (UTC). The list survives closing, reopening,
and later answers. A reconciliation mark records only the source and the
time it was made. It keeps any existing Desk answer and sets a pending
delivery to `cancelled`.
Removing the item removes its answer records.

A Desk answer also stores the current `answer` (plain text, up to 20,000
characters), `answered_at` (UTC), `delivery` (pending, sent, or cancelled),
and a stable `request_id` for inbox retries. The answer is saved before inbox
delivery. A correction gets a new request ID and adds a mark. Retrying
delivery reuses the same ID and mark.

Reports can read dated answers saved before `answer_marks` existed. An old
answered record with no source or timestamp counts as Elsewhere in the
all-time report. When that record changes, Quarterdeck keeps the prior mark
with `via` set to `elsewhere` and `answered_at` set to `null`.
