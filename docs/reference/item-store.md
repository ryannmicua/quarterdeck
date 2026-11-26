# Item store

Quarterdeck keeps saved items and Catch-up notices in one JSON file it owns:

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

Item and Catch-up timestamps (`saved_at` and, for notices, `read_at`) use
`YYYY-MM-DDTHH:MM:SSZ`. Served pages and rendered HTML display saved moments as
a date and time in the browser's local time zone when JavaScript is enabled;
without it they show the UTC date.

| Kind | `body` |
| --- | --- |
| `resource` | A `url` and `link_text`, or for two or more links `{"links": [{"text": "...", "url": "..."}, ...]}`. One link keeps the `url` and `link_text` shape, so older saved items need no rewrite. |
| `list` | `{"entries": ["...", "..."]}`, at least one. |
| `questions` | A nonempty `questions` array of objects. `question` and `context` are required text fields. `options` can be empty when the object has a `suggestion`. Each question may include `links`, an array of objects with exactly two fields, `text` and `url`. See [question images](#question-images) for option and attachment fields. |

After updating Quarterdeck, restart the service before saving a resource with
several links: an older running service cannot read the `links` shape. Rolling
back to an older version after saving such a resource is not supported.

A question may also carry `kind` (one word) and its own `tags`, which follow
the same list rules as item tags. An unclassified question omits `kind`.

Resource, question and Catch-up link URLs must use `http` or `https`, include
a hostname, and pass port validation. After trimming, a URL may contain at most
2,000 characters and cannot contain whitespace or backslashes.
Question link `text` must be nonempty and is limited to 200 characters after
whitespace is collapsed to single spaces. Quarterdeck omits an empty `links`
array when saving.

## Catch-up notices

Saving the first Catch-up notice adds both `next_catch_up` and the `catch_ups`
list. Both keys remain even when the list is empty. A notice ID is uppercase
`C` followed by a positive integer without leading zeros. The integer is
unique and less than `next_catch_up`, the number assigned to the next notice.
Quarterdeck never reuses a removed notice's ID.

These are the fields added to the version 1 store shown above:

```json
{
  "next_catch_up": 5,
  "catch_ups": [
    {
      "id": "C4",
      "title": "Tide review is ready",
      "summary": "The tide chart review is published and saved as a reference.",
      "saved_at": "2026-11-24T10:00:00Z",
      "read_at": "2026-11-24T18:30:00Z",
      "url": "https://example.com/review",
      "link_text": "Open the review",
      "item": "Q3",
      "project": "harbour"
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `title` | Nonempty text, up to 200 characters after whitespace is collapsed to single spaces. |
| `summary` | Nonempty text, up to 300 characters after whitespace is collapsed to single spaces. |
| `saved_at` | UTC time the notice was saved. |
| `read_at` | Optional. UTC time the notice was first marked read. A notice without it is unread. |
| `url` and `link_text` | Optional pair. The link text is nonempty and uses the same length and whitespace rules as the title. |
| `item` | Optional ID of a saved item. The notice links to the item while it exists. |
| `project` | Optional, with the same rules as an item's project. |

See [notice retention](../how-to/catch-up.md#how-long-a-notice-stays) for when
read notices are removed.

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
| Item page | Item descriptions, question prompts, context, options and suggested answers |
| Served item page | Ask and reply text in question threads, clarification text on question cards, and all messages in standalone conversations |

Quarterdeck uses the address as the link text. Inline links open in a new tab
so you can keep a draft answer open while following a link. Selecting an option
or using a suggestion still fills the answer with its plain text.

Separate multiple addresses with spaces so they are recognized individually.
Bare addresses lose trailing punctuation such as periods and exclamation
marks. Enclose an address in angle brackets, as in
`<https://example.com/?q=go!>`, to keep that punctuation in the destination.

## Message formatting

Message text is stored as written. On the served item page, messages in
question threads and standalone conversations show a few marks as formatting:

- A blank line starts a new paragraph.
- A line starting with `- ` or `* ` is a bullet, and a line starting with a
  number and a period, such as `2. `, is a numbered item. An indented line
  continues the item above it.
- A numbered list keeps the number written on its first item. It continues
  across blank lines while the numbers follow on, and a number that does not
  follow on starts a new list.
- `**words**` on one line shows in bold.

Everything else, including other Markdown, shows as plain text.

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

A failed `add`, `reply`, `post` or `converse`, or an interrupted `remove`, can leave files that no item names.
Retrying does not clean them up. Where the destination does not support hard
links, an interrupted image write during `add`, `reply`, `post`, `converse` or `render` can leave a partial
file. Delete that partial file by hand before retrying the write. See the
[serving boundary](../explanation/privacy-and-architecture.md#what-quarterdeck-reads-and-writes)
and [export behavior](cli-and-config.md#rendered-images).

## Conversations

A questions item may contain a `conversation` object with `item` for the
whole-item thread and one-based part numbers as string keys for threads
on individual questions. These threads are separate from `answers`.

A standalone `conversation` item requires a `conversation` object with only
the key `item`. Its `body` is `{}` and its `description` is a generic label.
Older descriptions are accepted and omitted from listings. The earliest
message with a `home` field binds the conversation. `converse` records that
field on the opening update when a home is configured. Otherwise the owner's
first message supplies it.

A conversation item may also carry `pinned`, which is `true` when the owner
pinned it. The field is optional, absent means unpinned, and only a
conversation may have it. Pinning and unpinning change nothing else about the
item, and an older store needs no change.

A conversation item may also carry `resolved` and `archived`, each `true` when
set. Both are optional, absent means not resolved and not archived, and only a
conversation may have them. They are independent of each other and of
`pinned`, and a message, a reply or an update changes none of them. A
store saved before these fields existed needs no change. A conversation's latest activity,
which orders the Desk, is the time of its newest message or its `saved_at`,
whichever is later.

A conversation item may also carry `task`, which makes it a task. It is an object
with `state` (`draft`, `started` or `review`), `description` (plain text, up to 20,000
characters) and optionally `source` (the ID of the conversation it was created from) and
`result`. `result` is `{"text": "...", "url": "..."}`, the link to what the task produced, and only a task
that is `review` may have it. Absent `task` means the conversation is not a task, only a conversation may have it,
and an older store needs no change. The tasks' state changes are the owner's Start, Approve and Changes needed
and Firstmate's `task review`; the `resolved` field stays separate, so a resolved task shows Resolved whatever its state.
A task in `draft` may have no messages yet, and `started` is set when the owner starts it and the message
`Task started.` is saved. A `source` must name an existing conversation other than the task itself.

A conversation item may also carry `placement`, an object with `project` (a
project ID such as `P1`) and optionally `topic` (a topic ID such as `T2` in
that project). Absent means ungrouped, and only a conversation may have it.
The store lists the owner's projects in an optional top-level `projects` array
of `{"id": "P1", "name": "Field Atlas", "topics": [{"id": "T1", "name": "Wayfinding"}]}`.
Names are up to 60 characters, and no two projects, or two topics in one
project, share a name when case is ignored. A project or topic keeps its ID when
renamed, and nothing deletes one. Moving or renaming changes nothing else about
a conversation, and an older store needs no change.

Each thread stores messages in the order they were saved. Every reply
names the earlier ask or message it answers.

Every message has `id` (a string of 32 hexadecimal characters), `author`, `text` (plain
text, up to 20,000 characters) and `at` (UTC).

| `author` | Other fields |
| --- | --- |
| `you` | An ask or conversation message with a browser-generated ID. `delivery` (`pending` or `sent`), a stable `request_id` string for inbox retries, and `home`, a hexadecimal `sha256` digest of the resolved path of the configured Firstmate home. |
| `firstmate` | `read` (true once the page acknowledges the message) and optional `links` (the same `text` and `url` objects as question links) and `images` (`file` and `alt`). A reply also has `ask`, the ID of an earlier message from `you` in the same thread. Standalone conversation updates omit `ask`. An opening update may carry `home` in the same digest format as a message from `you`. A reply to a question item as a whole may include `clarify`, which maps question numbers to `text` and `option_changed` (true or false). |

Clarification keys are one-based question numbers as strings. The clarification
text is plain text, up to 20,000 characters. Reply images use the item's
[private reply image folder](#store-layout).

## Question states

The stored `state` is Open, Answered or Closed. The served page derives
**Unread reply** from unread replies without changing the stored state. See
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
delivery reuses the same ID and mark. Reconfirming the same answer after a
newer option change updates its acceptance time and keeps its original mark.
Answers and option-change replies can carry fractional UTC seconds.

Corrections keep the superseded `answer`, original submission timestamp
(`answered_at`, UTC) and `request_id` in `earlier_answers`, in recording order.
Closing, reopening and reconciling the question preserve those entries.
Older records may omit the list.

Reports can read dated answers saved before `answer_marks` existed. An old
answered record with no source or timestamp counts as Elsewhere in the
all-time report. When that record changes, Quarterdeck keeps the prior mark
with `via` set to `elsewhere` and `answered_at` set to `null`.
