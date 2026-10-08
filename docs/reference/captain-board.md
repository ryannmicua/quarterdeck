# Captain board file

The captain board is a small JSON file that Firstmate writes whenever
something starts or stops needing the captain. Quarterdeck owns this schema,
reads the file, and never writes it.

## Location

`<firstmate home>/data/captain-board.json`, read for each configured home (or
that home's `data_dir`). If no configured home or registered secondmate has a
board file, Quarterdeck omits the captain board section.

## Schema (version 1)

```json
{
  "version": 1,
  "updated_at": "2026-10-08T10:00:00Z",
  "items": [
    {
      "id": "dock-merge",
      "group": "merge",
      "title": "Dock merge",
      "ask": "Merge the dock patch when you say so.",
      "detail": "Checks are green. Short **Markdown** is allowed.",
      "topic": "Harbor",
      "task": "dock-fix",
      "links": [{"label": "Pull request", "url": "https://example.com/pull/1"}]
    }
  ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `version` | yes | Must be integer `1`. Another value shows a warning and skips the board items. |
| `updated_at` | yes | ISO 8601 UTC time of the last write. Drives the age and stale flag. |
| `items[].id` | yes | Stable slug, unique within the file. |
| `items[].group` | yes | `merge`, `approve`, `decide`, `forward`, or `read`. |
| `items[].title` | yes | Short name the captain can say aloud. |
| `items[].ask` | yes | One plain-language line: what is wanted from the captain. |
| `items[].detail` | no | Short Markdown shown when the item is opened. |
| `items[].topic` | no | Sub-heading that groups `decide` items. |
| `items[].task` | yes | Backlog task id used for the cross-check. A `route/id` form names a registered secondmate's task. |
| `items[].links` | no | List of `{label, url}`; only `http` and `https` URLs are kept. Use it for the pull request or a Lavish page. A merge item's backlog PR link is added if no board link already points to it. |

## What the page does with it

- Leads with the groups in order: Ready to merge, Approvals and handovers,
  Decisions (sub-grouped by `topic`), Questions to forward, Reviews at your
  leisure. Empty groups are omitted. Items are numbered in display order so the
  captain can say a number; tapping an item opens its ask, detail and links.
- **Cross-check.** Every item must name a `task`. A matching readable backlog
  item remains visible while it needs the captain: a captain hold, a review-ready
  pull request, or blocked on the captain. A closed, cancelled, released, or
  unknown task counts as settled. Items without a `task` are invalid and skipped
  with a visible warning. If the backlog cannot be read, the item remains visible
  with a prominent warning. The page says how many items it hid.
- **Not yet sorted.** Open backlog items waiting on the captain that no board
  item references are listed last with their raw hold note, so nothing is lost.
  When the board file is present but unreadable, every such item appears here.
- **Age and staleness.** The header shows how long ago `updated_at` was, and
  flags the board as possibly out of date when the backlog file changed after
  `updated_at` (or `updated_at` is missing or invalid).
- **Malformed input.** Valid items still render. A bad file, timestamp, item,
  link, or detail URL produces a visible warning naming the problem;
  malformed detail is shown as text and the page never fails because of the board.
- **Unsupported version.** Board items are skipped with a warning naming the
  version; captain-gated backlog items from that home still appear under
  “Not yet sorted.”
- The reports-to-review list moves below the board in a collapsed section.
  The page reloads on its regular schedule and restores open items and sections
  after each refresh when browser session storage is available.

The page is laid out for a 375px phone and follows the system light or dark
setting. Quarterdeck makes no network calls to verify pull requests; settled
state comes only from the backlog.
