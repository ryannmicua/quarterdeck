# CLI and configuration reference

## Command

```text
quarterdeck render [--home PATH] [--output PATH] [--config FILE] [--title TEXT]
```

Homes are selected in this order: `--home`, the `homes` array in the config,
then `FM_HOME`. `--home` renders just that home and overrides a configured home
list. If the config has no `homes` array, the original single-home behavior
remains: set `FM_HOME` or pass `--home`. Without a selected home, the command
prints an error and exits with status 2.

For each configured home, Quarterdeck reads `data/backlog.md` and immediate
child `*/report.md` files. It also reads the parent's optional
`data/secondmates.md` registry and renders each registered local secondmate
under that parent. It does not run Firstmate commands or connect to remote
hosts. A missing or unreadable home in a configured list gets a failure section;
the other homes still render. The output page contains local `file:` links to
source reports and backlogs, and HTTPS links for GitHub pull requests.

| Option | Meaning |
| --- | --- |
| `--home PATH` | Firstmate home; overrides a configured `homes` list and `FM_HOME`. |
| `--output PATH` | Output HTML path; overrides `output_dir` in config. Keep it outside the repo. |
| `--config FILE` | Optional JSON configuration file. It may supply homes, title, and output directory. |
| `--title TEXT` | Page title; overrides `page_title` in config. |

`FM_DATA_OVERRIDE` applies to the single home selected by `--home` or
`FM_HOME`. In a configured home list, use each home's optional `data_dir`
instead.

Without an output override, the output is
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`.

## JSON configuration schema

Configuration is optional. Supply it with `--config`. Only these top-level
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

The held list recognizes structured backlog hold fields such as
`captain_actionable`, `hold_bucket`, `hold_kind`, `hold_reason`, and `hold_until`,
as well as held/needs-decision section labels. Review-ready pull requests need a
GitHub pull-request link and a review-ready state or flag. Recommendations are
shown only when a report explicitly labels a recommendation.

The page is regenerated on demand. `lavish-axi <output-file>` can open the
generated HTML as a local artifact.
