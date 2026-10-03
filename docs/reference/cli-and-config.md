# CLI and configuration reference

## Command

```text
quarterdeck render [--home PATH] [--output PATH] [--config FILE] [--title TEXT]
```

`--home` selects the Firstmate home. If omitted, `FM_HOME` must be set. Without
either value, or when the home/backlog is missing, the command exits with a
clear error. The data directory is `FM_DATA_OVERRIDE` when set, otherwise
`<home>/data`.

Quarterdeck reads `backlog.md` and immediate child `*/report.md` files. It does
not run Firstmate commands. The output page contains local `file:` links to the
source reports and backlog, and HTTPS links for GitHub pull requests.

| Option | Meaning |
| --- | --- |
| `--home PATH` | Firstmate home; overrides `FM_HOME`. |
| `--output PATH` | Output HTML path; overrides `output_dir` in config. Keep it outside the repo. |
| `--config FILE` | Optional JSON configuration file. |
| `--title TEXT` | Page title; overrides `page_title` in config. |

Without an output override, the output is
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`.

## JSON configuration schema

Configuration is optional. Supply it with `--config`. Only these keys are
accepted:

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `page_title` | string | `Quarterdeck` | Heading and browser title. |
| `output_dir` | string path | `${XDG_STATE_HOME:-~/.local/state}/quarterdeck` | Directory for `index.html`; `~` is expanded and relative paths use the current working directory. |

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
