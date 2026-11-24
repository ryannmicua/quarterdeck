# Item store

Quarterdeck keeps every saved item in one JSON file it owns:

```text
${XDG_STATE_HOME:-~/.local/state}/quarterdeck/items.json
```

Item commands and submitted answers update the store. Both set the store
directory to mode 0700, take an exclusive lock on `items.json.lock`, validate
the existing records, and atomically replace `items.json` with mode 0600.
Readers validate the records without taking the lock.

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
| `kind` | `resource`, `list` or `questions`. It does not change. |
| `title` | Up to 200 characters after whitespace is collapsed to single spaces. |
| `description` | Up to 600 characters describing what the item is and why it was saved. |
| `saved_at` | UTC time when the item was saved. |
| `body` | Depends on the kind; see below. |

| Kind | `body` |
| --- | --- |
| `resource` | A `url` and `link_text`. The URL must use `http` or `https`, include a hostname, and pass port validation. |
| `list` | `{"entries": ["...", "..."]}`, at least one. |
| `questions` | A nonempty `questions` array of objects. `question` and `context` are required text fields. `options` is an array of text and can be empty when the object has a `suggestion`. `links` is an optional array of `{"text": "...", "url": "..."}` objects, both required; `url` must be `http` or `https`, and anything else is refused when the item is saved. A link is shown with its text exactly as saved, directly above the options. |

Quarterdeck trims text when saving it and escapes it when building pages.
On the Saved page and individual item pages, standalone `http://` and
`https://` addresses in descriptions become links if they pass the same URL
validation as resource URLs. Quarterdeck uses the address as the link text.
Separate multiple addresses with spaces so they are recognized individually.
Bare addresses lose trailing punctuation such as periods and exclamation
marks. Enclose an address in angle brackets, as in
`<https://example.com/?q=go!>`, to keep that punctuation in the destination.

## Question answers

Questions items may contain an `answers` object keyed by the one-based part number as a string. Missing entries mean Open. Records carry `state` (open, answered or closed) and an opaque `revision` for detecting concurrent edits. Older items need no migration.

A browser answer also stores `answer` (plain text, up to 20,000 characters), `answered_at` (UTC), `delivery` (pending or sent) and a stable `request_id` for inbox retries. The answer is saved before delivery. A correction replaces the current record and gets a new request ID. The [`mark` command](cli-and-config.md#items) also refreshes the revision.
