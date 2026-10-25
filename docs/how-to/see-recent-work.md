# See recent work and the decisions still in play

Quarterdeck answers "what have we worked on in the last 12, 24 and 48 hours?"
and shows only the decisions still in play. Older and deferred decisions stay
out of the way until you ask for them.

## On the page

`render` and `serve` add a "Last 48 hours" section after the board. It has:

- one card per window (last 12 hours, 12–24 hours ago, 24–48 hours ago) with
  counts of merged work, started work and decisions you answered, then the
  merged and started items, newest first, with their project and PR link;
- "Decisions in play": open decisions raised, acted on by you, or worked on in
  the last 3 days;
- a collapsed "background decisions" toggle with quiet decisions; dated decisions stay hidden until due.

The board, Captain's Call, Underway, Charted Next, home cards and header
totals use the same relevance rule. Calls marked stale remain in the
background. Local secondmate calls missing from the parent feed use their
recorded backlog dates, board engagement and ledger activity.

A window the ledger does not fully cover says so. When a Firstmate source is
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
