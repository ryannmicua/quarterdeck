# Captain board file

The captain board is a small JSON file that Firstmate keeps current whenever
something starts or stops needing the captain, or the captain engages with it.
Quarterdeck owns this schema. `render` and `serve` only read the file; the
explicit [`quarterdeck board`](../how-to/keep-the-board-current.md) command is
the supported way to write it.

## Location

`<firstmate home>/data/captain-board.json`, read for each configured home (or
that home's `data_dir`). If no configured home or registered secondmate has a
board file, Quarterdeck omits the captain board section.

## Schema (version 1)

```json
{
  "version": 1,
  "updated_at": "2026-10-08T10:00:00Z",
  "operator": {"last_active_at": "2026-10-08T09:55:00Z", "session_started_at": "2026-10-08T08:10:00Z"},
  "items": [
    {
      "id": "dock-merge",
      "group": "merge",
      "title": "Dock merge",
      "ask": "Merge the dock patch when you say so.",
      "detail": "Checks are green. Short **Markdown** is allowed.",
      "topic": "Harbor",
      "task": "dock-fix",
      "links": [{"label": "Pull request", "url": "https://example.com/pull/1"}],
      "touched_at": "2026-10-08T09:30:00Z",
      "project": "dock-demo",
      "brief": {"url": "https://example.com/brief/dock", "recommendation": "Merge"}
    }
  ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `version` | yes | Must be integer `1`. Another value shows a warning and skips the board items. |
| `updated_at` | yes | ISO 8601 UTC time of the last write. Drives the age and stale flag. |
| `operator.last_active_at` | no | ISO 8601 time of the operator's latest message to Firstmate, written by `quarterdeck board active`. Groups the desk by session (below). |
| `operator.session_started_at` | no | ISO 8601 time of the first message of that session. Missing or invalid means the session starts at `last_active_at`. |
| `items[].id` | yes | Stable slug, unique within the file. |
| `items[].group` | yes | `merge`, `approve`, `decide`, `forward`, or `read`. |
| `items[].title` | yes | Short name the captain can say aloud. |
| `items[].ask` | yes | One plain-language line: what is wanted from the captain. |
| `items[].detail` | no | Short Markdown shown when the item is opened. |
| `items[].topic` | no | Topic label shown as a chip. |
| `items[].task` | yes | Backlog task id used for the cross-check. A `route/id` form names a registered secondmate's task. |
| `items[].links` | no | List of `{label, url}`; only `http` and `https` URLs are kept. Use it for the pull request or a Lavish page. A merge item's backlog PR link is added if no board link already points to it. |
| `items[].touched_at` | no | ISO 8601 time (UTC recommended; no zone means UTC) Firstmate raised the ask or the captain last engaged with the item or its project: discussed it, answered, commented on its brief, or asked about it. Defines conversation recency and drives the ranking below. |
| `items[].project` | no | Project name shown as a chip. Defaults to the backlog item's `repo`. |
| `items[].brief` | no | `{url, recommendation}`. `url` is an http(s) link to the [decision brief](../how-to/write-decision-briefs.md), shown as an **Open brief** button; `recommendation` is one short line shown as a chip. Either may be omitted. |

The `operator` object and the item fields `touched_at`, `project` and `brief`
were added without changing the version: older boards without them still parse, and readers that predate them ignore them. An
invalid value shows a warning and is ignored; the item still renders. Other
unknown keys are ignored by the page and kept by `quarterdeck board`.

## What the page does with it

The board leads the page, ranked by what the captain touched recently rather
than by group, so what they are working on comes first. When the board records
operator activity, the desk follows the captain's conversation with Firstmate:
it holds what was raised or discussed in the current session, and items from
earlier sessions drop to "This week" or "Parked" until they are touched again.

```text
conversation = the board item's touched_at; backlog dates do not establish conversation activity
touched   = newest of: touched_at,
                       the latest "Captain YYYY-MM-DD" note in the backlog item (future dates ignored),
                       the backlog item's "Captain hold set: <ISO time>"
deferred  = the backlog item has (hold-until: <date>) in the future,
            or a blocked-by / waiting-on / waiting-for field names another task in the same backlog,
            or its hold explicitly says "deferred until ...", "hold off ... until ...", or "waits until ...",
            or "waits/waiting on|for <token>" or "after <token>" in its hold names
               a different task in the same home's backlog (trailing punctuation stripped,
               task ids compared exactly; unknown tokens and self-references are not dependencies)
            Bare "parked" or "deferred" wording does not defer an item.

session      = from the operator record: last message L and session start S (see "Sessions" below)
               the newest record across all shown homes is used, because the operator is one person
On your desk : with a session: conversation at or after S and not deferred   open, no cap
               without one:    touched within 48 hours and not deferred     open, at most 5, then "+N more you touched recently"
This week    : not on the desk, touched within 7 days and not deferred     collapsed, counts per group
Parked       : older, never touched, or deferred          collapsed, counts per group
Within a band: merge > approve > decide > forward > read > not yet sorted, then newest first
               session desk uses conversation time; other bands and the legacy desk use touched
Weekly sweep : Parked items with a known touched time over 14 days ago get a "keep, defer or drop?" chip,
               and the Parked section leads with one line counting them
```

### Sessions

`quarterdeck board active` records each operator message. A message more than
**4 hours** after the previous one (or one recorded with `--new-session`)
starts a new session, so the session is the run of messages with no gap over
four hours. The page uses the same threshold to decide whether the operator is
still in that session.

**Active session** (last message 4 hours ago or less). The desk is headed
"On your desk" and grouped by when each item was raised or engaged
(its explicit board conversation time), newest group first:

| Group heading | Board conversation time |
| --- | --- |
| Open items from your last message | at or after the last message |
| Still waiting on you from N minutes ago | before the last message and under 1 hour ago; N is the newest item's age |
| Open items from an hour ago | before the last message and 1 hour to under 2 hours ago |
| Open items from this evening / this afternoon / this morning | before the last message, at least 2 hours ago, and earlier today: from 17:00, from 12:00, before 12:00 |
| Open items from yesterday | before the last message, at least 2 hours ago, and on the previous calendar day |
| Open items from earlier this session | before the last message, at least 2 hours ago, and before yesterday, in a session that spans days |

"Today" and the parts of the day use the local time zone of the machine
running Quarterdeck. Within a group, items keep the band order above. A
session bounds the desk, so it is not capped at five.

**Idle** (last message more than 4 hours ago). The page reads like a
`/bearings` catch-up: the desk is headed "Open items from our last session"
with every not-deferred item with a board conversation touch at or after that session began, ungrouped,
and "This week" starts open. Parked stays collapsed. The next recorded
message starts a new session, so items from the last session leave the desk
until they are touched again.

**No operator record** (Firstmate never ran `board active`, or the record is
invalid, which shows a warning). The desk uses the 48-hour rule.

Captain-directed asks such as "Waiting on the captain to approve" or "Waiting
for approval from the captain" do not establish a task dependency.

- Each item shows its group, project, topic, home (when several homes show),
  how long ago it was touched (conversation time on the session desk), any deferral reason, the brief recommendation
  chip, and an **Open brief** button. Tapping an item opens its ask, detail
  and links; nothing is shown only on hover.
- Items are numbered in display order. Numbers change as the order changes, so
  the title is the stable spoken handle.
- Collapsed sections show their counts on the closed heading, for example
  "3 merge · 5 decide · 8 read". The page remembers open sections across its
  refreshes.
- `quarterdeck render --all` opens every section and shows every desk item
  without the five-item cap.
- Touches after the last message, such as asks Firstmate raises while
  answering it, count as "from your last message".
- Day-precision notes count from the start of that UTC day.
- Future hold-set times are ignored; a future board `touched_at` is capped at
  the render time. Future captain-note dates do not defer an item.
- **Cross-check.** Every item must name a `task`. A matching readable backlog
  item remains visible while it needs the captain: a captain hold, a review-ready
  pull request, or blocked on the captain. A closed, cancelled, released, or
  unknown task counts as settled. Items without a `task` are invalid and skipped
  with a visible warning. If the backlog cannot be read, the item remains visible
  with a prominent warning. The page says how many items it hid.
- On bullet backlog entries, `hold-kind: captain` with a meaningful `hold`
  value counts as a captain hold when `held` is absent. If `held` is present,
  its value takes precedence, so a false value prevents stale hold text from
  keeping the item visible.
- **Not yet sorted.** Open backlog items waiting on the captain that no board
  item references are ranked into the same bands from their backlog dates,
  marked "Not yet sorted" and shown with their decoded hold note, so nothing is
  lost. Without a board conversation touch they stay in "This week" or
  "Parked" when operator activity is recorded; they can reach the desk only
  under the legacy 48-hour rule. When the board file is present but unreadable,
  every such item appears this way.
- **Age and staleness.** The header shows how long ago `updated_at` was, and
  flags the board as possibly out of date when the backlog file changed after
  `updated_at` (or `updated_at` is missing or invalid).
- **Malformed input.** Valid items still render. A bad file, timestamp, item,
  link, or detail URL produces a visible warning naming the problem;
  malformed detail is shown as text and the page never fails because of the board.
- **Unsupported version.** Board items are skipped with a warning naming the
  version; captain-gated backlog items from that home still appear as
  “Not yet sorted.”
- The reports-to-review list moves below the board in a collapsed section.
  The page reloads on its regular schedule and restores open items and sections
  after each refresh when browser session storage is available.

The page is laid out touch-first for a 375px phone: one column, tap targets
at least 44px, and the system light or dark setting. Quarterdeck makes no network calls to verify pull requests; settled
state comes only from the backlog.
