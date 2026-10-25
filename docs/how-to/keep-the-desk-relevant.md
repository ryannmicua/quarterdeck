# Keep the desk relevant

Sixty-nine reports are too many to read. Quarterdeck lets you (and Firstmate)
say which reports and past captain calls still matter, take reports off your
list without claiming you reviewed them, and surface older items that connect
to what you are working on now. All of this is stored in Quarterdeck's own
state database, never in a Firstmate home.

## Report states

| State | Meaning | How to set it |
| --- | --- | --- |
| needs review | Default. On the review list. | `reports unmark-reviewed ID`, `reports undiscard ID`, or any change to the report |
| reviewed | You read it. Shown under "Reviewed reports". | `reports mark-reviewed ID`, or **Mark reviewed** on the served page |
| discarded | Off the list; you did **not** review it. Shown under "Discarded reports". | `reports discard ID`, or **Discard** on the served page |

Reviewed and discarded replace each other. Both are tied to the report's
content: a report that changes afterwards returns to "needs review" on its
own. `undiscard` (or **Undiscard**) brings a discarded report back.

```sh
quarterdeck reports discard maple-harbor/amber-18
quarterdeck reports undiscard maple-harbor/amber-18
```

## Relevance: current or stale

Reports and past captain calls also carry a relevance mark. Anything
unclassified is **current**. Calls additionally follow the shared
[decision relevance rules](../reference/attention-json.md#decisions), so a
current mark does not by itself put a quiet or deferred call on the desk. **Stale**
means a one-off or already decided. Stale reports are left off the review list
and shown in a collapsed "Stale reports" section; stale calls leave the desk,
this week and parked bands and show in a collapsed "Marked stale" section.

Firstmate sets relevance as it learns a call was decided or a report was a
one-off:

```sh
quarterdeck reports set-relevance maple-harbor/amber-18 stale --reason "decided in chat"
quarterdeck reports set-relevance maple-harbor/amber-18 current
quarterdeck calls set-relevance dock-merge stale --reason "merged"
quarterdeck calls list
```

A call is named by its task ID, or by `<home-label-slug>/<task-id>` when the
same task ID exists in more than one home. Relevance is separate from
discarding: stale is Firstmate's judgement that something stopped mattering;
discard is your decision to stop reading it.

## Related to this session

Below the desk (never on it) a collapsed "Related to this session" section
lists older reports and captain calls that connect to what the active
session is working on, most related first, then newest. The session is the
desk's items plus in-flight backlog work. An item is related through:

- a relation Firstmate set explicitly (strongest);
- a task link: it is the report for a desk or in-flight task, waits on one, or
  one waits on it;
- a shared project, or a shared topic.

Each card says why it is there. Stale and discarded items are never listed.

Firstmate records explicit relations when it knows two things belong together:

```sh
quarterdeck reports relate maple-harbor/old-survey --to amber-18
quarterdeck reports relate maple-harbor/old-survey --to amber-18 --remove
quarterdeck calls relate pier-merge --to amber-18
```

`--to` names a backlog task ID. The relation matters while that task is on the
desk or in flight.

## Where it is stored

Marks, discards, relevance and relations live in
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/review-state/state.db`
(SQLite, mode 0600). An existing `marks.json` in that directory is imported once
the first time the database is opened and then left in place, unchanged.
