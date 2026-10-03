# CLI and configuration reference

## Command

```text
quarterdeck render [--home PATH] [--output PATH] [--config FILE] [--title TEXT] [--all] [--lavish]
quarterdeck add <firstmate-home> [--label LABEL] [--config FILE]
quarterdeck list [--config FILE]
quarterdeck remove <label-or-path> [--config FILE]
quarterdeck install [--uninstall]
quarterdeck help [COMMAND] [--json]
```

Run `quarterdeck --help` or `quarterdeck help` for a command overview.
`quarterdeck <command> --help` describes that command. `quarterdeck help
--json` prints the command index and each command's documentation paths for
tools that need to discover the interface. `quarterdeck install` runs the
user-local installer; add `--uninstall` to remove installer-created files.
The documentation tree is included in the checkout and its path is printed by
`quarterdeck help`.

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
under that parent. It does not run Firstmate commands or connect to remote
hosts. A missing or unreadable home in a configured list gets a failure section;
the other homes still render. The page links to readable HTML copies of
selected reports and backlogs, generated beside the main page, and HTTPS links
for GitHub pull requests.

| Option | Meaning |
| --- | --- |
| `--home PATH` | Firstmate home; overrides a configured `homes` list and `FM_HOME`. |
| `--output PATH` | Output HTML path; overrides `output_dir` in config. Keep it outside the repo and selected homes; those locations are refused. |
| `--config FILE` | JSON config for this command; defaults to the private user config where documented above. It may supply homes, title, and output directory. |
| `--title TEXT` | Page title; overrides `page_title` in config. |
| `--all` | Show every backlog item and report, including queued, finished, closed, and unlinked historical records. |
| `--lavish` | Write a separate `*.lavish.html` review page with stable IDs on review cards, then open it with `lavish-axi` when available. Without Lavish, print a command hint and succeed. |

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
separate Firstmate home. Remote routes are listed with an unavailable message
because Quarterdeck reads local files only. See [Configure multiple homes](../how-to/configure-multiple-homes.md)
for setup steps and [Privacy and architecture](../explanation/privacy-and-architecture.md)
for the read-only model.

Start from [`quarterdeck.example.json`](../../quarterdeck.example.json). It
contains invented example values only. Keep a real config file outside the repo
or name it `.quarterdeck.json`, which is ignored by Git.

## Data shown

The default attention view shows unresolved captain holds (held=yes,
hold_kind=captain, and not closed), review-ready GitHub pull requests, in-flight
work, and blocked items with a structured captain or external-party blocker.
Held cards show their recorded hold reason. A report is linked only when its
backlog item is held for the captain or marked review-ready; answered and closed
items no longer surface their reports. Queued and finished work is omitted from
cards. Use `--all` to restore the exhaustive view. Recommendations are shown
only when a report explicitly labels one.

The page is regenerated on demand. `quarterdeck render --lavish` writes a separate
Lavish-ready file beside the normal output (for example, `index.lavish.html`) and
opens it with `lavish-axi` when that command is on `PATH`. It prints Lavish's
session URL. Held, review-ready, and in-flight cards include a control that
queues a structured page request in that Lavish session. Quarterdeck does not
send the queued prompt, listen, poll, or create the requested page; an armed
listener must receive it and reply with the page link in the session
conversation panel. Without Lavish, Quarterdeck prints a command hint and
still succeeds.
