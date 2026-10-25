# ⚓ quarterdeck

### A local lookout for the work Firstmate is waiting to review.

## What it is

Quarterdeck turns running Firstmate homes' backlogs and scout reports into one
local HTML review page. Serving is read-only by default; report review marks are
available as an opt-in. When any selected home or registered secondmate has
a captain board, the "On your desk" list of what is waiting on you in your
current session with Firstmate leads the page; otherwise the page leads with
the four sections from Firstmate's `/bearings` digest. Reports waiting on your
review also appear. Configure several homes to review them together; each home
is grouped under a clear label, with its registered local secondmates under
their parent. Run `quarterdeck render --all` to restore the exhaustive view.

Render on demand, or run `quarterdeck serve` for a page that stays current on
its own. Open the result in a browser, or hand a rendered file to `lavish-axi`
as a local artifact. Quarterdeck itself has no agent or build step.

## Features

- Captain board: when a home has `data/captain-board.json`, the page leads with
  what was raised or discussed in the captain's current session with Firstmate,
  grouped from "Open items from your last message" back to "from yesterday";
  after a long idle gap it leads with "Open items from our last session" instead.
  Boards with no recorded operator activity use the earlier rule: what the
  captain touched in the last two days, at most five. Then come collapsed "This week" and "Parked" sections with counts per group. Items
  are cross-checked against the live backlog and can carry a decision-brief link
  and recommendation chip. Backlog items missing from the board still appear,
  marked "Not yet sorted". See the
  [board file reference](docs/reference/captain-board.md).
- Board writer: `quarterdeck board upsert|touch|remove <task-id>`,
  `quarterdeck board active` and `quarterdeck board list` let Firstmate keep the board current without
  hand-editing JSON; the writer changes only
  the board file. See [Keep the captain board current](docs/how-to/keep-the-board-current.md).
- Decision briefs: a touch-friendly, theme-aware
  [brief template](templates/decision-brief.html.tmpl) that Firstmate fills and
  publishes as a private Claude artifact. See
  [Write and publish a decision brief](docs/how-to/write-decision-briefs.md).
- Review buttons: start `quarterdeck serve --allow-marks` and use "Mark
  reviewed", "Discard", "Unmark" and "Undiscard" on the served page. They run
  the same code as `quarterdeck reports mark-reviewed`, `unmark-reviewed`,
  `discard` and `undiscard`; off by default. Discard takes a report off the
  list without claiming you reviewed it.
  See the [serving guide](docs/how-to/serve-the-page.md#mark-reports-reviewed-from-the-page).
- Reactions: with `--allow-marks`, each desk call, related item and report on
  the served page has Approve, Hold, Not now and Question buttons and a reply
  box. Send delivers a note to Firstmate through the home's `bin/fm-inbox.sh`.
  The cloud artifact has the same controls, recorded for
  `quarterdeck reactions import`, and a "Discuss with Firstmate" button that
  opens the claude.ai comment composer on the card.
- Relevance: Firstmate marks reports and past captain calls current or stale
  (`reports set-relevance`, `calls set-relevance`); stale ones are hidden by
  default behind a collapsed section. A collapsed "Related to this session"
  section below the desk surfaces older reports and deferred calls that share
  a project, topic, task link or explicit relation with the active session.
  See [Keep the desk relevant](docs/how-to/keep-the-desk-relevant.md).
- Cloud artifact: `quarterdeck export --artifact FILE` builds a Claude
  artifact page from [`templates/artifact.html.tmpl`](templates/artifact.html.tmpl)
  and a data snapshot; marks and discards made there come back with
  `quarterdeck import`. Clicking a card opens a detail view in the page (ask,
  recommendation, detail text, links, linked report text, reaction controls),
  and `--bodies desk` keeps the snapshot small by carrying report text only for
  reports tied to the desk. A status line in the header and at the top of the
  desk says whether reactions are connected. See
  [Use the cloud artifact](docs/how-to/use-the-cloud-artifact.md).
- Compact header: the page opens straight on the desk.
- Bearings-shaped page: Captain's Call, Recently Landed, Underway, and Charted
  Next, each always rendered with an empty-state sentence.
- Captain's Call cards include local hold context, options, and recommendations
  when available. Remote decisions show the snapshot summary and point to their
  secondmate home for the full background.
- Last 48 hours: what merged and started in the last 12 hours, 12–24 hours
  ago and 24–48 hours ago, from Firstmate's fleet ledger, and only the
  decisions still in play; deferred and quiet decisions sit behind a toggle.
  `quarterdeck attention --json` prints the same view for other readers, and
  `quarterdeck attention --check` validates Firstmate's formats after an
  update. See [See recent work](docs/how-to/see-recent-work.md).
- Uses Firstmate's own `bin/fm-bearings-snapshot.sh --json` when a home has it,
  and says so when it falls back to parsing the backlog.
- `quarterdeck serve` serves the page over HTTP. `quarterdeck service` can
  print or write a systemd user unit; see the
  [serving guide](docs/how-to/serve-the-page.md).
- Scout reports with an explicit recommendation when the report states one.
- Stable report IDs, Markdown and readable HTML reading, and local review state (reviewed, discarded, relevance) in a private SQLite database.
- Reports needing review appear independently of backlog attention.
- In-flight tasks and pull requests marked ready for review.
- Attention filtering, with `quarterdeck render --all` restoring queued, finished, closed, and historical items.
- Combined pages for configured homes, with registered secondmates grouped
  under their parent.
- Inline-styled review page with readable local report and backlog pages.
- Optional `quarterdeck render --lavish` creates and opens an annotation-ready Lavish review page.
- Lavish request controls queue a report or backlog-item page request for an armed session listener.
- Python 3 standard library only.
- Generated pages default to the user's state directory outside the repo.

## Quick Start: Make Ready to Sail

Install Quarterdeck, register a Firstmate home, and render your lookout page:

```sh
curl -fsSL https://raw.githubusercontent.com/ryannmicua/quarterdeck/main/install.sh | sh
quarterdeck add ~/firstmate
quarterdeck render
```

For a read-first install, download `install.sh`, read it, then run it. Here
`~/firstmate` is a placeholder for the path to your Firstmate home. Run
`quarterdeck help` for command help or `quarterdeck help --json` for the command
index.

You should see Quarterdeck installed, your home registered, and the path to the
rendered page. Open that printed `index.html` in your browser. By default, it
lives at `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`; run
`quarterdeck render` again whenever you want a fresh page. To update the
installation later, run `quarterdeck update`.

For the first-render walkthrough, installation, update and refresh how-tos, and
command reference, see the [tutorial](docs/tutorials/first-render.md),
[how-to guides](docs/how-to/), and [CLI reference](docs/reference/cli-and-config.md).
Use `quarterdeck reports list` to find a report ID, then run
`quarterdeck reports read <report-id>` to print it and generate its readable
page. See the [report review how-to](docs/how-to/review-reports.md) for review
commands.

## How it relates to Firstmate

Firstmate runs the crew and stores its backlog and scout reports in a home.
Quarterdeck reads those files and creates a convenient review surface alongside
that workflow. It does not extend Firstmate or send answers back to it.
Regeneration happens when `quarterdeck render` runs or `quarterdeck serve`
receives its first page request after the 30-second cache expires.

## Privacy model

The page can contain real task names, recommendations, and paths. By default,
Quarterdeck writes it outside the repository under the user's state directory.
Keep custom output paths outside the repository and selected homes, and keep
real configs outside the repository. See
[privacy and architecture](docs/explanation/privacy-and-architecture.md) for
the files Quarterdeck reads, its optional Firstmate snapshot, and the local-only
mode.

The repo ignores common output folders, local config files, and screenshots.
Run `python3 scripts/privacy_guard.py --staged` before committing; it rejects
generated pages, likely real-data files, host-specific paths, private IPs, and
pull-request links or ticket-shaped keys in staged content. Examples and tests
use invented data.

## FAQ

### Does Quarterdeck keep refreshing in the background?

Yes. `quarterdeck serve` refreshes the page while it is open. See the
[serving guide](docs/how-to/serve-the-page.md) for cache timing and systemd
setup. Without `serve`, run `render` whenever a fresh page is useful, or
schedule it; see
[scheduled refresh](docs/how-to/schedule-refresh.md).

### Is it safe to bind `serve` to my network?

The default is 127.0.0.1. A non-loopback `--host` exposes your real work data
to that network with no login. Keep the default unless you control the network.
With `--allow-marks`, anyone who can open the page can mark or unmark reports
and send reactions to Firstmate; there is no key. See the
[serving guide](docs/how-to/serve-the-page.md#mark-reports-reviewed-from-the-page).

### Does it cost tokens?

No. Quarterdeck does not call a model. See
[privacy and architecture](docs/explanation/privacy-and-architecture.md) for
the optional snapshot's data access.

### Does it contact a service?

See [privacy and architecture](docs/explanation/privacy-and-architecture.md)
for Quarterdeck's network behavior, optional snapshot, and the `--no-snapshot`
mode.

### How do I select several homes?

Register each home with `quarterdeck add`, then run `quarterdeck render`. Use
`quarterdeck list` to see entries and `quarterdeck remove` to unregister one.
These commands use `${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json` by default;
pass `--config FILE` to select another config. See the
[multiple homes how-to](docs/how-to/configure-multiple-homes.md).

### What if there is no Firstmate home setting?

Run `quarterdeck add /path/to/firstmate`, pass `--home /path/to/firstmate`, set
`FM_HOME`, or provide a config with `homes`. Quarterdeck stops with a clear
error if none is set.

## Roadmap

Possible future work: a pinned desk artifact for reading off the local
network, a Listen button and tap-to-answer on briefs, an agent-driven marking
path, multi-user review history, Firstmate extension binding, and hosted or
authenticated views; see the [captain desk experience log](docs/explanation/captain-desk-experience-log.md).
These are out of scope today. Apart from the opt-in report review marks, the local
state commands, and the explicit `quarterdeck board` writer, Quarterdeck remains a read-only view of
Firstmate homes.
