# Attention JSON (`quarterdeck-attention.v1`)

`quarterdeck attention --json` prints one JSON object: the work of the last 48
hours in three windows, and the open decisions split into the ones still in
play and the background. The page's "Last 48 hours" section renders this same
object, so the page, `quarterdeck attention`, and any reader such as Cadence
give the same answer.

```text
quarterdeck attention --json [--all-decisions] [--project NAME] [--more] [--preferences FILE] [--home PATH] [--config FILE]
quarterdeck attention --check [--home PATH] [--config FILE]
```

Homes are selected as for `render`: `--home`, then the config's `homes`, then
`FM_HOME`. Registered local secondmates contribute backlog decisions absent
from the parent's feed. Their board and ledger activity applies equally to
feed-backed and fallback decisions.
Quarterdeck does not run additional scripts in those homes.

## Sources

Quarterdeck computes the view from these recorded sources and its existing
private review state:

| Source | Read how | Understood version |
| --- | --- | --- |
| `state/fleet-ledger.jsonl` | read directly | record version `1` ([Firstmate fleet ledger contract](https://github.com/kunchenguid/firstmate/blob/main/docs/fleet-ledger.md)) |
| `data/backlog.md` | read directly, including registered local secondmates | existing backlog formats |
| Quarterdeck review state | read locally | existing call relevance marks |
| `data/captain-board.json` | read directly, including registered local secondmate boards | board version `1`; engagement uses `touched_at` |
| `bin/fm-attention.sh --json` | run with a 15-second timeout | schema `fm-attention.v1` |

Titles come from the home's `data/backlog.md`, then the feed's decision and
in-flight titles, then the task id.

Quarterdeck refuses a source it does not understand instead of guessing:

- any ledger line whose `v` is missing or not `1` stops the ledger read;
- a ledger line that is not a JSON object with a numeric `ts`, a string
  `event`, and a string `task` stops the ledger read, except an unterminated
  final line, which is an append still being written and is skipped;
- a feed whose `schema` is not `fm-attention.v1`, or whose `open_decisions`
  rows lack `task`, `kind`, or `title`, is refused.

Identical ledger records count once. Ledger events Quarterdeck does not use
are ignored, as the contract requires.

## Exit status

| Status | Meaning |
| --- | --- |
| `0` | Every selected home's sources were read. |
| `1` | At least one source is missing, unreadable, or in a version Quarterdeck does not understand. The JSON is still printed, with the error. |
| `2` | Usage or configuration error, such as no home selected or neither `--json` nor `--check`. |

A home with no ledger file is not an error: recording is off, every window's
`coverage` is `none`. Decisions still use the recorded activity described
[below](#decisions), including feed touches, board engagement and local
secondmate ledgers.

## Object

| Member | Meaning |
| --- | --- |
| `schema` | `quarterdeck-attention.v1` |
| `generated` | UTC time of the read, ISO 8601, second precision. Windows and relevance are measured back from it. |
| `ok` | `true` when no home reported an error. |
| `priority_projects` | The saved priority projects (up to three), in priority order. |
| `requested_project` | The `--project` name, else `null`. |
| `homes` | One entry per selected home, in selection order. |

Readers must ignore members they do not recognize; later versions may add
members without changing the schema name.

### Home entry

| Member | Meaning |
| --- | --- |
| `label`, `home` | The home's label and absolute path. |
| `ok`, `errors` | `errors` lists `{source, code, message}` objects; `ok` is `true` when it is empty. |
| `ledger` | `{path, present, version, records}`, or `null` when the ledger could not be read. `records` counts distinct records. |
| `recent_work` | See below, or `null` when the ledger could not be read. |
| `decision_relevance` | `{recent_days, since}`: the relevance window. |
| `recent_work_6h` | Merged and started items of the last six hours, newest first; each is a recent-work item plus `event` (`merged` or `started`). `[]` when none or the ledger could not be read. |
| `show_recent` | `true` only when `recent_work_6h` is not empty. Readers skip recent work otherwise. |
| `waiting`, `waiting_scope`, `more_count` | See [Waiting](#waiting-and-priorities). |
| `priorities` | See [Waiting](#waiting-and-priorities). |
| `decisions` | See below, or `null` when no feed was read and no backlog candidates were found (except with `--no-snapshot`). Source failures remain in `errors` even if backlog decisions are available. |

Error `source` is `home`, `backlog`, `ledger`, or `decisions`. Error `code` is
one of `home_not_local`, `backlog_unreadable`, `ledger_unreadable`,
`ledger_malformed`, `ledger_version`, `feed_missing`, `feed_failed`,
`feed_malformed`, or `feed_schema`. Show `message` to the reader as it is.

### Recent work

`recent_work` has `ledger_starts_at`, the time of the oldest ledger record or
`null`, and `windows`, newest first:

| `window` | `from` | `to` |
| --- | --- | --- |
| `last_12h` | 12 hours before `generated` | `generated` |
| `12h_to_24h` | 24 hours before `generated` | 12 hours before `generated` |
| `24h_to_48h` | 48 hours before `generated` | 24 hours before `generated` |

A window holds records stamped at or after `from` and before `to`;
`last_12h` also holds records stamped after `generated`.

Each window has `window`, `from`, `to`, `coverage`, `merged_count`,
`started_count`, `answered_count`, `merged`, and `started`.

- `coverage` is `full` when the ledger starts at or before `from`, `partial`
  when it starts inside the window, and `none` when it starts at or after `to`
  or there is no ledger. Do not present a window that is not `full` as
  complete.
- `merged` lists the window's `task.merged` records and `started` its
  `task.dispatched` records, newest first; the counts are their lengths.
- `answered_count` counts `captain.answered` records with mode `done` or
  `release`. A call closed as moot (`reconciled`) is not an answer.

Each item has `at`, `task`, `title`, `project`, and `pr`; a `merged` item adds
`via` (`pr` or `local`) and a `started` item adds `kind`. `title` is plain
text with links removed. `project` is the project from the task's newest
`task.dispatched` record, else the backlog item's repository, else `null`.
`pr` is the record's own PR URL, else the task's newest PR URL in the ledger,
else the backlog item's PR URL, else `null`.

### Decisions

`decisions` has `source` (`fm-attention.v1`, or `backlog` when no feed was
read), `relevant`, `background_count`, and `background`. With
`render --no-snapshot`, `skipped` records why the feed was skipped; local
backlog decisions still use the same classification.

Feed rows preserve their fields and order, adding `relevance` and `activity_at`. Calls absent
from the feed are appended from local backlogs with `source: backlog` and a
home-qualified `task`. Their dates and deferrals come from the backlog;
activity comes from the local or parent ledger and board engagement.
A row is relevant when it is neither deferred nor marked stale
and, within the last 3 days (`DEFAULT_RECENT_DAYS` in `quarterdeck.py`):

| `relevance` | Rule, first match wins |
| --- | --- |
| `deferred` | The feed's `bucket` is `dated`: held until a later date. Never relevant, whatever its activity. |
| `stale` | Quarterdeck marks the call stale. Background regardless of activity. |
| `raised` | Its `opened_at` falls in the window. |
| `captain_activity` | Its `last_touched_at`, a board item's `touched_at` for the same task, or a ledger `captain.*` record falls in the window. |
| `recent_work` | The ledger has any other record for its task in the window. |
| `quiet` | None of the above. Background. |

`activity_at` is the newest recorded timestamp in the relevance window, or
`null` when no recent activity qualifies or the decision is deferred. A stale
mark can send a row to the background while retaining its activity timestamp.
Worker events retain their actual times and do not imply that the captain
discussed the decision.

`relevant` lists the `raised`, `captain_activity`, and `recent_work` rows.
`background_count` counts the rest. `background` is `null` unless
`--all-decisions` is passed, which lists the background rows there.

Firstmate does not record the captain's chat messages, so only mentioning a
call does not make it relevant; holding, deferring, or answering it does, and
so does work on its task.

### Waiting and priorities

`waiting` has three lists, in the order to say them: `decisions` (open decisions
including quiet, nondeferred decisions), `needs_you` (the rest of what needs the captain, from the
board: merge, approve, decide, forward and unsorted board and backlog items not
already a decision), and `informational` (board `read` items). Each item has
`kind`, `key`, `title`, `ask`, `group`, `project`, `at` and `priority` (index
into the priority projects, or `null`). Items projected from decisions also
retain `relevance`, so quiet decisions can remain collapsed without affecting
the pending-work count.

With priority projects set, items for them come first in priority order. When
any exist across the selected homes, `waiting_scope` is `priority`, only those items are listed, and
`more_count` counts the held-back items, available in the same three lists
under `waiting_more`; `--more` lists everything and sets
`waiting_scope` to `all`. When none exist for the priorities, or none are set,
everything is listed (`waiting_scope` `all`, `more_count` `0`).

`priorities` has one entry per priority project (or the single `--project`
name): `project`, `nothing_waiting`, `waiting` (all matching items across the
three lists, unaffected by scope) and `say`, a short sentence such as
"harbor-app: nothing is waiting." When required sources fail or the feed is
skipped, an empty result has `nothing_waiting: null` and says "waiting status
unavailable" instead. Projects match case-insensitively on the
recorded project name or the part after `owner/`.

These members were added to schema `quarterdeck-attention.v1` without changing
existing members. The preference file is
`~/.config/quarterdeck/preferences.json`: `{"version": 1, "priority_projects": [...]}`;
a file in another version, or with more than three names, exits 2.

## `--check`

`--check` reads the same sources and prints one line, exiting with the status
above:

```text
quarterdeck attention check: ok — main: ledger v1, 340 records from 2026-07-01T06:00:00Z; fm-attention.v1, 11 open decisions
quarterdeck attention check: FAILED — main: ledger: <home>/state/fleet-ledger.jsonl line 16 has record version 2; Quarterdeck understands only version 1
```

Firstmate runs `quarterdeck attention --check` after each Firstmate update; see [See recent work](../how-to/see-recent-work.md).
