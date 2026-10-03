# ⚓ quarterdeck

### A local lookout for the work Firstmate is waiting to review.

## What it is

Quarterdeck turns a running Firstmate home's backlog and scout reports into one
read-only HTML review page. It puts held items first, then review-ready pull
requests, scout reports, and active tasks. Every item links to its report,
pull request, or backlog file.

Render on demand, open the returned file in a browser, or hand it to
`lavish-axi` as a local artifact. Quarterdeck has no daemon, agent, build step,
or runtime network dependency.

## Features

- Primary review list for held items and recorded decisions.
- Scout reports with an explicit recommendation when the report states one.
- Active backlog items and pull requests marked ready for review.
- One self-contained page with inline styling and local file links.
- Python 3 standard library only; no tokens and no write-back to Firstmate.
- Generated pages default to the user's state directory outside the repo.

## Quick Start

Quarterdeck needs Python 3 and a Firstmate home. Give the home explicitly with
`--home`, or set `FM_HOME`:

```sh
python3 quarterdeck.py render --home /path/to/firstmate
```

The command prints the generated page path. Open it in a browser, or run
`lavish-axi /path/to/generated/index.html`. The default output is
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`.

For installation, updates, removal, a first-render walkthrough, configuration,
and scheduled refresh examples, see the [documentation](docs/).

## How it relates to Firstmate

Firstmate runs the crew and stores its backlog and scout reports in a home.
Quarterdeck reads those files and creates a convenient review surface alongside
that workflow. It does not extend Firstmate or send commands to it. Regeneration
happens only when `quarterdeck render` is run.

## Privacy model

The page can contain real task names, recommendations, and paths. By default,
Quarterdeck writes it outside the repository under the user's state directory.
It reads the configured Firstmate home's `data/backlog.md` and
`data/<id>/report.md` files; it does not write to that home. Keep custom output
paths outside the repository too.

The repo ignores common output folders, local config files, and screenshots.
Run `python3 scripts/privacy_guard.py --staged` before committing; it rejects
generated pages, likely real-data files, host-specific paths, private IPs, and
pull-request links or ticket-shaped keys in staged content. Examples and tests
use invented data.

## FAQ

### Does Quarterdeck keep refreshing in the background?

No. Run `render` whenever a fresh page is useful. A cron or systemd timer can
run that command on a schedule; see [scheduled refresh](docs/how-to/schedule-refresh.md).

### Does it need credentials or contact a service?

No. Rendering reads local files and writes a local HTML file. The page uses
inline CSS and has no runtime network requests.

### What if there is no Firstmate home setting?

Pass `--home /path/to/firstmate` or set `FM_HOME`. Quarterdeck stops with a
clear error if neither is set.

## Roadmap

Possible future work: optional write-back, answer capture, listener or daemon,
Firstmate extension binding, and hosted or authenticated views. These are out of
scope today; Quarterdeck remains a local read-only renderer.
