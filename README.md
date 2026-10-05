# ⚓ quarterdeck

### A local lookout for the work Firstmate is waiting to review.

## What it is

Quarterdeck turns running Firstmate homes' backlogs and scout reports into one
read-only HTML review page that leads with the same four sections as
Firstmate's `/bearings` digest, plus the reports waiting on your review. Configure several homes to review them together;
each home is grouped under a clear label, with its registered local secondmates
under their parent. It shows unresolved captain holds, review-ready pull
requests, in-flight work, and items blocked on the captain or an external
party. Use `--all` to restore the exhaustive view.

Render on demand, or run `quarterdeck serve` for a page that stays current on
its own. Open the result in a browser, or hand a rendered file to `lavish-axi`
as a local artifact. Quarterdeck itself has no agent, build step, or runtime
network dependency.

## Features

- Bearings-shaped page: Captain's Call, Recently Landed, Underway, and Charted
  Next, each always rendered with an empty-state sentence, plus a prominent
  "Reports waiting on your review" list.
- Every Captain's Call item explains itself: full hold reason and task body,
  options and recommendation, when it was filed and how long it has waited,
  project, and a link to its report.
- Uses Firstmate's own `bin/fm-bearings-snapshot.sh --json` when a home has it,
  and says so when it falls back to parsing the backlog.
- `quarterdeck serve` re-renders from your homes on each request (30-second
  cache), reloads itself about every 60 seconds, binds 127.0.0.1 by default,
  and is GET-only. `quarterdeck service` writes a systemd user unit for it.
- Primary review list for held items and recorded decisions.
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
- Python 3 standard library only; no tokens and no write-back to Firstmate.
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
receives a page request.

## Privacy model

The page can contain real task names, recommendations, and paths. By default,
Quarterdeck writes it outside the repository under the user's state directory.
It reads each configured Firstmate home's `data/backlog.md` and
`data/<id>/report.md` files. It also reads the parent's optional
`data/secondmates.md` registry and the listed local secondmate homes. It does
not otherwise write to those homes or to the Quarterdeck checkout. The one
command it may run in a home is that home's own
`bin/fm-bearings-snapshot.sh --json`. Quarterdeck itself makes no network calls,
but Firstmate's snapshot may read registered remote secondmate ledgers through
its routes and cache; those returned rows appear in the page. `--no-snapshot`
uses the locally read files only. Keep custom output
paths outside both and real configs outside the repository too.

The repo ignores common output folders, local config files, and screenshots.
Run `python3 scripts/privacy_guard.py --staged` before committing; it rejects
generated pages, likely real-data files, host-specific paths, private IPs, and
pull-request links or ticket-shaped keys in staged content. Examples and tests
use invented data.

## FAQ

### Does Quarterdeck keep refreshing in the background?

Yes, if you run `quarterdeck serve`: it re-renders from the homes on each page
request and the open page reloads itself. `quarterdeck service` prints or writes
a systemd user unit to keep it running; see
[serve the page](docs/how-to/serve-the-page.md). Without `serve`, run `render`
whenever a fresh page is useful, or schedule it; see
[scheduled refresh](docs/how-to/schedule-refresh.md).

### Is it safe to bind `serve` to my network?

The default is 127.0.0.1. A non-loopback `--host` exposes your real work data
to that network with no login. Keep the default unless you control the network.

### Does it cost tokens?

No. Nothing calls a model; it only reads files and, when present, runs a home's
own deterministic `bin/fm-bearings-snapshot.sh`.

### Does it contact a service?

Quarterdeck itself makes no network calls, and the page has no runtime network
requests. Its snapshot command may read registered remote secondmate ledgers
through Firstmate's own routes and cache. Use `--no-snapshot` to skip that
command and classify from locally read files only.

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

Possible future work: optional write-back, answer capture, Firstmate extension
binding, and hosted or authenticated views. These are out of scope today;
Quarterdeck remains a read-only view.
