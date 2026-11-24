# Item store

Quarterdeck keeps every saved item in one JSON file it owns:

```text
${XDG_STATE_HOME:-~/.local/state}/quarterdeck/items.json
```

The file is mode 0600 and its directory 0700. Change it only with the
`quarterdeck` commands: they lock the store, check every value, and replace the
file in one step, so a reader never sees half a write.

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
      "description": "The two questions holding the tide launch.",
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
| `id` | `Q` and a number. Parts are cited as `Q3.1`, `Q3.2` in the order they are stored. |
| `kind` | `resource`, `list` or `questions`. It does not change. |
| `title` | Up to 200 characters on one line. |
| `description` | Up to 600 characters: what the item is and why it was saved. |
| `saved_at` | UTC time when the item was saved. |
| `body` | Depends on the kind; see below. |

| Kind | `body` |
| --- | --- |
| `resource` | `{"url": "https://...", "link_text": "..."}`. Only `http` and `https` addresses. |
| `list` | `{"entries": ["...", "..."]}`, at least one. |
| `questions` | `{"questions": [{"question", "context", "options", "suggestion"}]}`. `question` and `context` are required, and at least one option or a suggestion is required. The stored `options` array can be empty when a suggestion is present. |

Quarterdeck trims text when saving it and displays it as escaped plain text.
