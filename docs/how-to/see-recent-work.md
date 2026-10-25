# See recent work and the decisions still in play

Quarterdeck answers "what have we worked on in the last six hours?" (shown only
when there is some) and shows only the decisions still in play. Older and deferred decisions stay
out of the way until you ask for them.

## On the page

`render` and `serve` add a "Waiting on you" section after the board. It has:

- "Priority projects": for each project you set (see below), its waiting items,
  or "nothing is waiting";
- waiting decisions, then "Needs you", then "For your information", scoped to
  your priorities; other waiting items stay behind a counted "more" toggle;
- a collapsed "background decisions" toggle with quiet decisions; dated decisions stay hidden until due;
- "Last 6 hours": what merged and started in the last six hours, newest first,
  with project and PR link. This part appears only when there is such work;
  older work is skipped and only the waiting items show.

The board, Captain's Call, Underway, Charted Next, home cards and header
totals use the same relevance rule. Calls marked stale remain in the
background. Local secondmate calls missing from the parent feed use their
recorded backlog dates, board engagement and ledger activity.

When a Firstmate source is
missing or in a version Quarterdeck does not understand, the section shows a
warning instead of an empty view.

The section needs the home's fleet ledger turned on (Firstmate's
`config/fleet-ledger` flag) and Firstmate's `bin/fm-attention.sh`. With
`--no-snapshot`, recent work and local backlog decisions still show; the
Firstmate scripts are skipped.

## From the command line

```sh
quarterdeck attention --json
```

prints the view as JSON with the background decisions counted. When you want
to go through the background decisions too:

```sh
quarterdeck attention --json --all-decisions
```

The shared relevance window is 3 days (`DEFAULT_RECENT_DAYS` in `quarterdeck.py`). The fields are in
the [attention JSON reference](../reference/attention-json.md).

## After a Firstmate update

Firstmate may change the formats Quarterdeck reads. Firstmate runs this check
after each Firstmate update:

```sh
quarterdeck attention --check
```

It prints one line. `ok` means both sources are still in the versions
Quarterdeck understands. `FAILED` names the source and the problem, and exits
with status 1; report that line, and do not trust the page's recent-work
section until Quarterdeck is updated.

## Priority projects

Name up to three important projects once; Quarterdeck reads
them from `~/.config/quarterdeck/preferences.json` (or under `XDG_CONFIG_HOME`).
Cadence adopts this preference file in its own change.

```sh
quarterdeck priorities set harbor-app reef-demo
quarterdeck priorities show
quarterdeck priorities clear
```

`set` refuses more than three names and names absent from Firstmate's route
registry project entries and waiting items. With priorities set, `quarterdeck attention --json`
lists the waiting items for those projects first, in priority order, and holds
back the rest, reporting `more_count`; pass `--more` to include them. When
nothing is waiting for the priority projects, the rest is shown. Each priority
entry has a `say` sentence such as "harbor-app: nothing is waiting." Use
`--project NAME` to ask about one project instead.
