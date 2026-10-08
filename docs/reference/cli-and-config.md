# CLI and configuration reference

## Command

```text
quarterdeck render [--home PATH] [--output PATH] [--config FILE] [--title TEXT] [--all] [--lavish] [--no-snapshot]
quarterdeck serve [--host ADDRESS ...] [--port N] [--home PATH] [--config FILE] [--title TEXT] [--no-snapshot] [--allow-marks]
quarterdeck service [--write] [--allow-marks] [--host ADDRESS ...] [--port N] [--home PATH] [--config FILE]
quarterdeck url [--json]
quarterdeck reports list [--home PATH] [--config FILE]
quarterdeck reports read <report-id> [--home PATH] [--config FILE] [--open]
quarterdeck reports mark-reviewed <report-id> [--home PATH] [--config FILE]
quarterdeck reports unmark-reviewed <report-id> [--home PATH] [--config FILE]
quarterdeck add <firstmate-home> [--label LABEL] [--config FILE]
quarterdeck list [--config FILE]
quarterdeck remove <label-or-path> [--config FILE]
quarterdeck install [--uninstall]
quarterdeck update
quarterdeck help [COMMAND] [--json]
```

Run `quarterdeck --help` or `quarterdeck help` for a command overview.
`quarterdeck <command> --help` describes that command. `quarterdeck help
--json` prints the command index and each command's documentation paths for
tools that need to discover the interface. `quarterdeck install` runs the
user-local installer; add `--uninstall` to remove installer-created files.
`quarterdeck update` runs that same installer path to update the installed
checkout. The documentation tree is included in the checkout and its path is
printed by `quarterdeck help`.

The private default config is
`${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json`. `render` reads it when it
exists; `add`, `list`, and `remove` use it by default. `render`, `add`, `list`,
and `remove` accept `--config FILE` to select another config. `add` creates a
missing file and parent directory, and `add` and `remove` write atomically with
mode `0600`. Config writes inside the Quarterdeck checkout are refused.

Homes for `render` are selected in this order: `--home`, the `homes` array in
the selected config, then `FM_HOME`. `--home` renders just that home and
overrides a configured home list. If there is no configured home list, the
single-home behavior remains: set `FM_HOME` or pass `--home`. Without a
selected home, the command prints an error and exits with status 2.

`add` requires a directory containing `data/backlog.md`. It expands `~`,
normalizes the path to an absolute path from the current working directory, and
refuses duplicate normalized paths and labels. The default label is the last
path component; the command prints that choice. `list` prints registered
labels and paths. `remove` accepts a label (case-insensitive) or normalized
path and only unregisters the entry; it never changes Firstmate data.

For each configured home, Quarterdeck reads `data/backlog.md` and immediate
child `*/report.md` files. It also reads the parent's optional
`data/secondmates.md` registry and renders each registered local secondmate
under that parent. It may run that home's
`bin/fm-bearings-snapshot.sh --json` for the bearings view, unless
`--no-snapshot` is set. Quarterdeck itself makes no network calls and invokes
no other Firstmate script directly. The snapshot wrapper may read registered
remote secondmate ledgers through Firstmate's routes and cache, and Quarterdeck
shows the returned rows. A missing or unreadable home in a configured list
gets a failure section;
the other homes still render. The page links to readable HTML copies of
selected reports and backlogs, generated beside the main page, and HTTPS links
for GitHub pull requests.

`reports list`, `reports read`, `reports mark-reviewed`, and
`reports unmark-reviewed` use the selected homes in the config, or the single
home from `--home` or `FM_HOME`. They also accept `--config FILE`. Report IDs
have the exact form `<home-label-slug>/<task-id>`: the label slug is the
case-folded label with each run of characters outside `a` through `z` and `0`
through `9` replaced by `-`, then leading and trailing hyphens removed; the
task ID is the report's immediate parent directory name unchanged. The slug is
uncapped. An empty slug becomes `home`. For example, `Maple Harbor` and `amber-18`
produce `maple-harbor/amber-18`. A collision appears as `ID collision` in the
list. Read and review commands refuse the ambiguous ID with matching homes and
paths; use distinct home-label slugs to resolve it. The list reports home,
secondmate registry, and unreadable report-file errors; report ID commands
refuse resolution while a selected local home is incomplete.

`reports read` prints Markdown and writes the existing readable HTML format.
The HTML page is written to the Quarterdeck state directory. Use `--open` to
open the generated page in a browser. `mark-reviewed` records the current
report fingerprint; `unmark-reviewed` clears that mark. See [Privacy and architecture](../explanation/privacy-and-architecture.md)
for review-state storage and [The attention model](../explanation/attention-model.md)
for how marks affect the dashboard.

| Option | Meaning |
| --- | --- |
| `--home PATH` | Firstmate home; overrides a configured `homes` list and `FM_HOME`. |
| `--output PATH` | Output HTML path; overrides `output_dir` in config. Keep it outside the repo and selected homes; those locations are refused. |
| `--config FILE` | JSON config for this command; defaults to the private user config where documented above. It may supply homes, title, and output directory. |
| `--title TEXT` | Page title; overrides `page_title` in config. |
| `--all` | Show every backlog item and report, including queued, finished, closed, and unlinked historical records. |
| `--lavish` | Write a separate `*.lavish.html` review page with stable IDs on review cards, then open it with `lavish-axi` when available. Without Lavish, print a command hint and succeed. |

| `--no-snapshot` | Do not run a home's `bin/fm-bearings-snapshot.sh`; classify from the backlog only. |

`serve` re-renders the page and its readable pages at startup and on the first
request after its 30-second cache expires. It serves them with `GET` by default
on each repeatable `--host` address (default `127.0.0.1` only) and `--port`
(default 8765; valid ports are 1 through 65535). `--allow-marks` enables the
key-protected report review request described in the
[serving guide](../how-to/serve-the-page.md); all other writes remain refused.
The served page reloads itself every 60 seconds and shows its generated-at
time. `service` prints a systemd user unit that runs `serve` with the given
options, or with `--write` writes it to
`${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service`; it never runs
`systemctl`. A non-loopback host exposes work data without a login. `url`
prints the address or addresses configured in the installed user
service; it does not check whether the service is running. If no unit is
installed, it labels the default `serve` address shown, which a plain
`quarterdeck serve` uses. A foreground serve started with custom `--host` or
`--port` is not detected and serves wherever those flags point. IPv6 URLs use
brackets, and scoped IPv6 zone separators use `%25` in URLs. Add `--json` for
an object containing `urls`, raw configured `hosts`, and `source`; when no unit
is installed, `source` is `default` and `note` explains the fallback. See
[Serve the always-current bearings page](../how-to/serve-the-page.md).

`add`, `list`, and `remove` accept the same `--config FILE` option. `add` also
accepts `--label LABEL`; `remove` takes one label or path.

`FM_DATA_OVERRIDE` applies to the single home selected by `--home` or
`FM_HOME`. In a configured home list, use each home's optional `data_dir`
instead.

Without an output override, the output is
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`.

## JSON configuration schema

Configuration is optional for rendering a single home. Only these top-level
keys are accepted:

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `page_title` | string | `Quarterdeck` | Heading and browser title. |
| `output_dir` | string path | `${XDG_STATE_HOME:-~/.local/state}/quarterdeck` | Directory for `index.html`; `~` is expanded and relative paths use the current working directory. |
| `homes` | array of objects | unset | Homes to render together. Each object has `path` (required), `label` (optional), and `data_dir` (optional). |

Each home object's `path` is the Firstmate home. `label` is shown above its
review sections; if omitted, Quarterdeck uses the last path component. An
optional `data_dir` replaces that home's `<path>/data` for its backlog and
reports. Home `path` and `data_dir` values expand `~`; relative values resolve
from the config file's directory. Relative `output_dir` keeps its original
behavior and resolves from the current working directory.

Example:

```json
{
  "page_title": "Quarterdeck",
  "homes": [
    {"label": "Maple Harbor", "path": "~/firstmate-maple"},
    {"label": "Willow Quay", "path": "~/firstmate-willow"}
  ]
}
```

Local secondmates are discovered from one parser-compatible route per line in
the parent's `data/secondmates.md`; each route's `home:` field points to a
separate Firstmate home. Remote routes are listed as unavailable homes, though
the parent's snapshot may return their aggregated records for the bearings
sections. See [Configure multiple homes](../how-to/configure-multiple-homes.md)
for setup steps and [Privacy and architecture](../explanation/privacy-and-architecture.md)
for the read-only model.

Start from [`quarterdeck.example.json`](../../quarterdeck.example.json). It
contains invented example values only. Keep a real config file outside the repo
or name it `.quarterdeck.json`, which is ignored by Git.

## Data shown

Recommendations are shown only when a report explicitly labels one. See [The
attention model](../explanation/attention-model.md) for dashboard inclusion and
`--all` behavior.

The page is regenerated on demand. `quarterdeck render --lavish` writes a
separate Lavish-ready file beside the normal output (for example,
`index.lavish.html`) and opens it with `lavish-axi` when that command is on
`PATH`. It prints Lavish's session URL. Without Lavish, Quarterdeck prints a
command hint and still succeeds. See [Request a Lavish page](../how-to/request-lavish-page.md)
for the workflow and [Privacy and architecture](../explanation/privacy-and-architecture.md)
for the request data and interaction boundary.
