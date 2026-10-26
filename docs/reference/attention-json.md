# Attention JSON (`quarterdeck-attention.v1`)

`quarterdeck attention --json` prints one JSON object with waiting queues,
project priorities, and recent work. The page's "Waiting on you" section renders
this shared view. Readers use `recent_work_6h` and `show_recent` for recent-work
display; the older three-window `recent_work` member remains for compatibility.

```text
quarterdeck attention --json [--all-decisions] [--project NAME] [--more] [--view [NAME]] [--preferences FILE] [--home PATH] [--config FILE]
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
| `requested_project` | The resolved recorded name for `--project`, else `null`. |
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
| `done_recent` | Completions for the activity cards, including registered local secondmates; see [Recent completions](#recent-completions). |
| `show_recent` | `true` only when `recent_work_6h` is not empty. Readers skip recent work otherwise. |
| `waiting`, `waiting_more`, `waiting_scope`, `more_count` | See [Waiting](#waiting-and-priorities). |
| `priorities` | See [Waiting](#waiting-and-priorities). |
| `decisions` | See below, or `null` when no feed was read and no backlog candidates were found (except with `--no-snapshot`). Source failures remain in `errors` even if backlog decisions are available. |

Error `source` is `home`, `backlog`, `ledger`, or `decisions`. Error `code` is
one of `home_not_local`, `backlog_unreadable`, `ledger_unreadable`,
`ledger_malformed`, `ledger_version`, `feed_missing`, `feed_failed`,
`feed_malformed`, or `feed_schema`. Show `message` to the reader as it is.

### Recent completions

`done_recent` includes `task.merged` events and `captain.answered` events with
mode `done`, `release`, or `reconciled`, within the inclusive six-hour window
ending at `generated`. It keeps the latest qualifying event per task per home.
A finished backlog item without such an event contributes a completion when
its first parseable `merged`, `done`, or `closed` attribute falls in that window.
Homes whose ledger cannot be read contribute no completions; a missing ledger
still permits dated backlog completions. The list is empty when none qualify.

Items have `at`, `task`, `title`, `project`, `pr`, `home` (the owning home's label),
and `event`: `merged`, `answered`, `closed` (mode `reconciled`), or `finished`
(backlog completion). Ledger-derived items also have `via`. Task IDs remain
local to their owning home. This list is independent of waiting priority scope
and is not globally sorted; the activity feed sorts it for display. It does not
change the saved briefing view's `done` section described below.

### Recent work

`recent_work_6h` includes events at or after six hours before `generated` and
at or before `generated`; future events are excluded. Each item uses the fields
described below, plus `event` (`merged` or `started`). `show_recent` says whether
this list contains any items.

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
into the effective project list, or `null`). `kind` is `decision`, `needs_you`,
or `informational`. Items projected from decisions also
retain `relevance`, so quiet decisions can remain collapsed without affecting
the pending-work count.

With priority projects set, items for them come first in priority order. When
any exist across the selected homes, `waiting_scope` is `priority`, only those items are listed, and
`more_count` counts the held-back items, available in the same three lists
under `waiting_more` (these held-back items omit `priority`); `--more` lists everything and sets
`waiting_scope` to `all`. When none exist for the priorities, or none are set,
everything is listed (`waiting_scope` `all`, `more_count` `0`).

`--project` replaces the saved projects for scoping, using the same fallback
when nothing matches. Its effective project list has just that resolved name.

`priorities` has one entry per priority project (or the single `--project`
name): `project`, `nothing_waiting`, `waiting` (all matching items across the
three lists, unaffected by scope and without the `priority` field) and `say`, a short sentence such as
"harbor-app: nothing is waiting." When required sources fail or the feed is
skipped, an empty result has `nothing_waiting: null` and says "waiting status
unavailable" instead. This also applies when a local home or route registry
cannot be read, or a board is unsupported or has parsing warnings.
Project names match case-insensitively. The set command and explicit project
queries resolve short names to a recorded name before filtering; an exact
recorded name takes precedence, followed by the first matching `owner/name`
in alphabetical order. Scoping then matches that full recorded name.

These members were added to schema `quarterdeck-attention.v1` without changing
existing members. See the [CLI and configuration reference](cli-and-config.md)
for preference storage, validation, and command options.

## Briefing views

`--view NAME` (or `--view` alone for the startup view) adds one `view` member
and changes nothing else. Without the flag the output has no `view` member.
An unknown name exits 2 and lists the saved views. Views are saved beside the
priority projects; see the [CLI reference](cli-and-config.md) for the commands
and the two seeded views, Morning and Priorities. Quarterdeck builds the
sections from every selected home so readers only draw them.

| `view` member | Meaning |
| --- | --- |
| `name`, `default` | The view's name, and whether it is the startup view. |
| `available` | The names of all saved views, in saved order. |
| `layout` | The saved sections: `{type}` or `{type: "project", project}`. |
| `sections` | The built sections, top first, as below. |

Each section has `type` (`waiting`, `recent`, `project`, `information` or `done`; a
saved `priorities` section becomes one `project` section per priority project),
`title`, `project` (project sections only), `needs_you` and `information` (item
lists), `background`, `shown_above`, `nothing_waiting` and `say`, a short
sentence fit to be read aloud.

- `waiting` holds every Needs you item (decisions and the rest of the board's
  needs-you items). Quiet and stale decisions are not listed there; they
  become `background`, `{count, say, items}` with `say` such as "4 older
  decisions waiting". `background` is `null` in every other section and when
  there are none.
- `recent` holds the last six hours of merged and started work in
  `information`, ordered by status tier, priority-project order, then newest
  activity, each item with `event`, `at`, `task`, `title`,
  `project`, `pr`, `key` and `home`. The section is left out of `sections` entirely
  when nothing merged or started in six hours. The status tier comes from the
  task's waiting or informational queue when present; otherwise merged work
  ranks as Result ready and started work as In flight.
- `done` holds merged tasks from the last six hours, ordered by status tier,
  priority-project order, then newest activity, using the same task identities
  and first-section claiming as every other section.
- `project` holds that project's `needs_you` items, then its `information`
  items. When nothing is waiting on it, both lists are empty,
  `nothing_waiting` is `true` and `say` is "Nothing waiting on <project>.";
  `nothing_waiting` is `null` with "<project>: waiting status unavailable."
  when a required source failed, as for `priorities`.
- `information` holds the informational (board `read`) items and work recorded
  as in flight or queued in a home's backlog that is not already waiting on the captain.

An item shows once per screen. Sections are built in order; the first section
that claims an item lists it and each later section that would have listed it
counts it in `shown_above` ("1 shown above" in `say`), including recent work. `nothing_waiting` is `false` whenever
the section lists something or counted something shown above.

Outside `recent` and `done`, items in `needs_you` and `information` have
`key`, `kind`, `title`, `ask`,
`project`, `home` (the home label), `status`, `status_label`, `at` and
`background`. Within a section they sort by status tier, then by priority
project in the set order (others last), then newest first. The tiers, in
order, are:

| `status` | `status_label` | Taken from |
| --- | --- | --- |
| `decision_waiting` | Decision waiting | an open decision, or a board `decide` or unsorted item |
| `blocked` | Blocked | a board `forward` item |
| `pr_ready` | PR ready to merge | a board `merge` item |
| `review_waiting` | Review waiting | a board `approve` item |
| `result_ready` | Result ready | a board `read` item |
| `in_flight` | In flight | a backlog task recorded as in flight |
| `queued` | Queued | other open, noncancelled backlog work not already waiting on the captain; deferred decisions are excluded |

The served page uses the same builder for every saved view; the page's data is
not part of this JSON. Readers must ignore members they do not recognize.

## `--check`

`--check` reads the same sources and prints one line, exiting with the status
above:

```text
quarterdeck attention check: ok — main: ledger v1, 340 records from 2026-07-01T06:00:00Z; fm-attention.v1, 11 open decisions
quarterdeck attention check: FAILED — main: ledger: <home>/state/fleet-ledger.jsonl line 16 has record version 2; Quarterdeck understands only version 1
```

Firstmate runs `quarterdeck attention --check` after each Firstmate update; see [See recent work](../how-to/see-recent-work.md).
