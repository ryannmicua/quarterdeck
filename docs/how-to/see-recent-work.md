# See recent work and the decisions still in play

Quarterdeck answers "what have we worked on in the last six hours?" (shown only
when there is some) and shows waiting decisions. Without priorities, quiet and
stale decisions stay collapsed; dated decisions stay hidden until due.

## On the page

`render` and `serve` add a "Waiting on you" section after the board. Select a
saved briefing view to choose which sections appear and in what order (see
[Briefing views](#briefing-views) below). The
[view reference](../reference/attention-json.md#briefing-views) defines section
contents, ordering, background decisions and project availability messages.

The board, Captain's Call, Underway, Charted Next, home cards and header
totals use the same relevance rule. Outside priority queues, calls marked stale
remain in the background. Local secondmate calls missing from the parent feed use their
recorded backlog dates, board engagement and ledger activity.

When a Firstmate source is missing or in a version Quarterdeck does not
understand, the section shows a warning alongside any available items.

The section needs the home's fleet ledger turned on (Firstmate's
`config/fleet-ledger` flag) and Firstmate's `bin/fm-attention.sh`. With
`--no-snapshot`, recent work and local backlog decisions still show; the
Firstmate scripts are skipped.

## From the command line

```sh
quarterdeck attention --json
```

prints the view as JSON. Waiting queues include nondeferred background decisions;
to also populate the separate background decision list:

```sh
quarterdeck attention --json --all-decisions
```

The fields and relevance rules are in the
[attention JSON reference](../reference/attention-json.md).

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

Name up to three important projects once; Quarterdeck saves them in its
preference file (see the [CLI and configuration reference](../reference/cli-and-config.md)).
Cadence adopts this preference file in its own change.

```sh
quarterdeck priorities set harbor-app reef-demo
quarterdeck priorities show
quarterdeck priorities clear
```

Choose names recorded in Firstmate's route registry project entries or waiting
items. Use the Priorities view on the page to see those projects first.
The CLI's waiting queues use the
[priority scoping rules](../reference/attention-json.md#waiting-and-priorities).
Ask for more or ask about one project:

```sh
quarterdeck attention --json --more
quarterdeck attention --json --project harbor-app
```

## Briefing views

A view is an ordered stack of sections you choose: Waiting on you, Recent work
(last six hours), one section per priority project, For your information, and Done recently
(merged work in the last six hours).
Quarterdeck ships two, Morning (opens at startup) and Priorities. On the page, a
Views rail (a row of buttons on a phone) switches between them; "Edit view"
lets you tick sections, move them up or down, rename, and tick "Use at
startup". Saving from the page needs `quarterdeck serve --allow-marks`; without
it, use the CLI:

```sh
quarterdeck views list
quarterdeck views save Reef --section project:reef-demo --section waiting --default
quarterdeck views default Morning
quarterdeck attention --json --view Priorities
```

An item shows once, in the first section that claims it; later sections say
"1 shown above". A project with nothing waiting says "Nothing waiting on
<project>." Recent work drops out when nothing merged or started in six hours.

See [Waiting and priorities](../reference/attention-json.md#waiting-and-priorities)
for JSON scoping and project status fields.
