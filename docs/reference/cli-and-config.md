# CLI and configuration reference

## Commands

```text
quarterdeck add --kind resource|list|questions --title TEXT --description TEXT
                [--url URL --link-text TEXT] [--body-file PATH|-]
quarterdeck list
quarterdeck show ID
quarterdeck mark ID[.PART] answered|closed|open
quarterdeck remove ID
quarterdeck render [--output PATH] [--config FILE] [--title TEXT]
quarterdeck serve [--host ADDRESS ...] [--port N] [--base-path PATH] [--tls-cert FILE --tls-key FILE] [--config FILE] [--title TEXT]
quarterdeck service [--write] [--host ADDRESS ...] [--port N] [--base-path PATH] [--tls-cert FILE --tls-key FILE] [--config FILE]
quarterdeck url [--json]
quarterdeck install [--uninstall]
quarterdeck update
quarterdeck help [COMMAND] [--json]
```

An `ID` is the displayed ID in either case, such as `Q7` or `q7`.
Item commands report validation errors on stderr and exit with status 2.

### Items

| Command | What it does |
| --- | --- |
| `add` | Saves a new item and prints its ID. A `resource` needs `--url` and `--link-text`. A `list` or `questions` item needs `--body-file`. See [the input formats and examples](../how-to/save-items.md#add-an-item). |
| `list` | Prints saved items, newest first, with ID, kind, saved date and title. |
| `show` | Prints one item in full, numbering list entries and questions as `Q7.1`, `Q7.2`. |
| `mark` | Sets the state of one question or every question in a questions item. It preserves any saved answer, timestamp and delivery status. See [Read answers](../how-to/read-answers.md) for examples. |
| `remove` | Deletes an item. Its ID is never used again. |

### Pages

| Command | What it does |
| --- | --- |
| `render` | Writes `index.html` (Saved) and one `q<N>.html` page per item. `--output PATH` names the Saved page and puts the item pages beside it. Otherwise the pages go to `output_dir`. Item filenames `q<N>.html` are reserved. Existing destination files must carry Quarterdeck's generated-page marker. Render retires only marked item pages that are no longer needed. Separately named Saved pages are kept and share the item pages in that directory. The output must be outside the Quarterdeck checkout. |
| `serve` | Serves the same pages over HTTP, or HTTPS with `--tls-cert` and `--tls-key`. Page requests read the store, so a new item shows on the next load. Open item Q7 at `/q7.html`. `--host` may be repeated. `--port` defaults to 8765. See [serving security](../how-to/serve-the-page.md#security-loopback-by-default) for the default bind and browser access rules. |
| `service` | Prints a systemd user unit with the supplied serving options, or writes it to `${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service` with `--write`. |
| `url` | Prints the address the installed unit serves, or the default address when no unit is installed. |

A served page also has a **Workers** section when `firstmate_home` is set. The
page asks `GET workers.json` (under any `base_path`) on entry and on **Refresh
workers**. The endpoint runs the home's `bin/fm-fleet-snapshot.sh --local`,
a read-only command that uses no model, with `FM_HOME` set to that home and a
60 second limit. It answers 502 if a read is already running or the command
fails, times out or prints something unexpected. It answers 404 when
`firstmate_home` is absent. Quarterdeck writes nothing to the home.

| Field in `workers.json` | Meaning |
| --- | --- |
| `observed_at` | UTC time the snapshot was taken. |
| `workers` | Tasks that have a live terminal, other than second mates. Each has `name`, `project`, `activity`, `status`, `active_at` (UTC) and `links`. |
| `second_mates` | Every second mate in the home. Each has the same fields, with `contact_at` (UTC) in place of `active_at`. |

`status` is one of Working, Question asked, Stuck, Finished, Unreachable or
Unknown, mapped only from facts the snapshot states: an open decision gives
Question asked; the states `working`, `done`, and `blocked` or `failed` give Working,
Finished and Stuck; a second mate whose terminal is not running is Unreachable;
anything else, including a stale reading, is Unknown. See
[Read the workers section](../how-to/read-workers.md#what-the-statuses-mean).

Rendered item pages include any saved answers and their timestamps and
delivery status. Answer controls are available on served pages.

If `XDG_STATE_HOME` is set when you run `service`, the generated unit includes
that setting so it reads the same [item store](item-store.md).

`--base-path /qd` (or `base_path` in the config) serves every page under that
prefix for a reverse proxy. A request for the bare prefix is redirected (308)
to `/qd/`, and a path outside the prefix to the same path beneath it, such as
`/q3.html` to `/qd/q3.html`. `--base-path /` turns a configured prefix off.

`serve` still accepts `--allow-marks`, `--watch-waits`, `--no-snapshot` and
`--home`, which older service units pass, and ignores them with a notice.

### Install and help

`install` runs the user-local installer; `install --uninstall` removes what it
created. `update` updates an installed checkout. `help` prints command help and
the documentation to read; `help --json` prints a machine-readable index.

## JSON configuration

The config is optional. `render`, `serve` and `service` read `--config FILE`,
or `${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json` when it exists.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `page_title` | string | `Quarterdeck` | The site name in the navigation bar and browser title. `--title` overrides it for `render` and `serve`. |
| `output_dir` | string path | `${XDG_STATE_HOME:-~/.local/state}/quarterdeck` | Where `render` writes the pages. |
| `base_path` | string | none | The URL prefix described above. |
| `firstmate_home` | absolute path after expanding `~` | see legacy fallback below | Local destination for answers submitted to `serve`, and the home whose workers the page lists. |

When `firstmate_home` is absent, `serve` uses a legacy `homes` list only for
answer delivery and only if it contains exactly one object with a valid
`path`. Relative legacy paths are resolved from the config file's directory.
Without a destination, Quarterdeck saves answers with delivery pending.
`captain_waits` is ignored.
Any other key is an error. See
[`quarterdeck.example.json`](../../quarterdeck.example.json).
