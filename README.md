# ⚓ quarterdeck

### A local lookout for the work Firstmate is waiting to review.

## What it is

Quarterdeck turns running Firstmate homes' backlogs and scout reports into one
read-only HTML review page. When any selected home or registered secondmate has
a captain board, the grouped list leads the page; otherwise the page leads with
the four sections from Firstmate's `/bearings` digest. Reports waiting on your
review also appear. Configure several homes to review them together; each home
is grouped under a clear label, with its registered local secondmates under
their parent. Run `quarterdeck render --all` to restore the exhaustive view.

Render on demand, or run `quarterdeck serve` for a page that stays current on
its own. Open the result in a browser, or hand a rendered file to `lavish-axi`
as a local artifact. Quarterdeck itself has no agent or build step.

## Features

- Captain board: when a home has `data/captain-board.json`, the page leads with
  a grouped, numbered, phone-friendly "waiting on you" list (merge, approve,
  decide, forward, read), cross-checked against the live backlog, with anything
  unlisted under "Not yet sorted". See the
  [board file reference](docs/reference/captain-board.md).
- Review buttons: start `quarterdeck serve --allow-marks` and use "Mark
  reviewed" and "Unmark" on the served page. They run the same code as
  `quarterdeck reports mark-reviewed` and `unmark-reviewed`; off by default.
  See the [serving guide](docs/how-to/serve-the-page.md#mark-reports-reviewed-from-the-page).
- Bearings-shaped page: Captain's Call, Recently Landed, Underway, and Charted
  Next, each always rendered with an empty-state sentence.
- Captain's Call cards include local hold context, options, and recommendations
  when available. Remote decisions show the snapshot summary and point to their
  secondmate home for the full background.
- Uses Firstmate's own `bin/fm-bearings-snapshot.sh --json` when a home has it,
  and says so when it falls back to parsing the backlog.
- `quarterdeck serve` serves the page over HTTP. `quarterdeck service` can
  print or write a systemd user unit; see the
  [serving guide](docs/how-to/serve-the-page.md).
- Scout reports with an explicit recommendation when the report states one.
- Stable report IDs, Markdown and readable HTML reading, and local review marks.
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
With `--allow-marks`, anyone who can load the page can also mark reports
reviewed, so use it only on loopback or a network you control.

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

Possible future work: marking backlog and board items as seen, an agent-driven
marking path, multi-user review history, write-back to homes, answer capture, Firstmate extension
binding, and hosted or authenticated views. These are out of scope today;
apart from the opt-in report review marks, Quarterdeck remains a read-only view.
