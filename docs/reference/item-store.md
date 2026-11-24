# Item store

Quarterdeck keeps every saved item in one JSON file it owns:

```text
${XDG_STATE_HOME:-~/.local/state}/quarterdeck/items.json
```

Change the store only with the `quarterdeck` commands. They set the store
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
| `description` | Up to 600 characters: what the item is and why it was saved. |
| `saved_at` | UTC time when the item was saved. |
| `body` | Depends on the kind; see below. |

| Kind | `body` |
| --- | --- |
| `resource` | A `url` and `link_text`. The URL must use `http` or `https`, include a hostname, and pass port validation. |
| `list` | `{"entries": ["...", "..."]}`, at least one. |
| `questions` | A nonempty `questions` array of objects. `question` and `context` are required text fields. `options` is an array of text and can be empty when the object has a `suggestion`. |

Quarterdeck trims text when saving it and displays it as escaped plain text.
