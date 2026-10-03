# ⚓ quarterdeck

### A local lookout for the work Firstmate is waiting to review.

## What it is

Quarterdeck turns running Firstmate homes' backlogs and scout reports into one
read-only HTML review page. Configure several homes to review them together;
each home is grouped under a clear label, with its registered local secondmates
under their parent. It puts held items first, then review-ready pull requests,
scout reports, and active tasks. Every item links to its report, pull request,
or backlog file.

Render on demand, open the returned file in a browser, or hand it to
`lavish-axi` as a local artifact. Quarterdeck has no daemon, agent, build step,
or runtime network dependency.

## Features

- Primary review list for held items and recorded decisions.
- Scout reports with an explicit recommendation when the report states one.
- Active backlog items and pull requests marked ready for review.
- Combined pages for configured homes, with registered secondmates grouped
  under their parent.
- One self-contained page with inline styling and local file links.
- Python 3 standard library only; no tokens and no write-back to Firstmate.
- Generated pages default to the user's state directory outside the repo.

## Quick Start: Make Ready to Sail

Install Quarterdeck, register a Firstmate home, and render your lookout page:

```sh
curl -fsSL https://raw.githubusercontent.com/ryannmicua/quarterdeck/main/install.sh | sh
quarterdeck add ~/firstmate
quarterdeck render
```

You should see Quarterdeck installed, your home registered, and the path to the
rendered page. Open that printed `index.html` in your browser. By default, it
lives at `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`; run
`quarterdeck render` again whenever you want a fresh page.

For the first-render walkthrough, installation and refresh how-tos, and command
reference, see the [tutorial](docs/tutorials/first-render.md),
[how-to guides](docs/how-to/), and [CLI reference](docs/reference/cli-and-config.md).

## How it relates to Firstmate

Firstmate runs the crew and stores its backlog and scout reports in a home.
Quarterdeck reads those files and creates a convenient review surface alongside
that workflow. It does not extend Firstmate or send commands to it. Regeneration
happens only when `quarterdeck render` is run.

## Privacy model

The page can contain real task names, recommendations, and paths. By default,
Quarterdeck writes it outside the repository under the user's state directory.
It reads each configured Firstmate home's `data/backlog.md` and
`data/<id>/report.md` files. It also reads the parent's optional
`data/secondmates.md` registry and the listed local secondmate homes. It does
not write to those homes. Keep custom output paths and real configs outside
the repository too.

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

Possible future work: optional write-back, answer capture, listener or daemon,
Firstmate extension binding, and hosted or authenticated views. These are out of
scope today; Quarterdeck remains a local read-only renderer.
