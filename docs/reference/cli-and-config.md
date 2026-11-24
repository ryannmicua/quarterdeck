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
| `add` | Saves a new item and prints its ID. A `resource` needs `--url` and `--link-text`. A `list` or `questions` item needs `--body-file`. Questions may carry images, which are copied into the private store. See [the input formats and examples](../how-to/save-items.md#add-an-item). |
| `list` | Prints saved items, newest first, with ID, kind, saved date and title. |
| `show` | Prints one item in full, numbering list entries and questions as `Q7.1`, `Q7.2`. Each question link is printed with its text and URL. |
| `mark` | Sets the state of one question or every question in a questions item. It preserves any saved answer, timestamp and delivery status. See [Read answers](../how-to/read-answers.md) for examples. |
| `remove` | Deletes an item and its saved images. Its ID is never used again. |

### Pages

| Command | What it does |
| --- | --- |
| `render` | Writes `index.html` (Saved) and one `q<N>.html` page per item. `--output PATH` names the Saved page and puts the item pages beside it. Otherwise the pages go to `output_dir`. The names `q<N>.html` and `images` are reserved. Existing destination pages must carry Quarterdeck's generated-page marker. Render uses the live images in the default state directory and copies images to `images/<id>/` for external output. It retires obsolete marked item pages. It never deletes an image, so an external output keeps the copies of removed items. Separately named Saved pages are kept and share the item pages in that directory. The output must be outside the Quarterdeck checkout. |
| `serve` | Serves the Saved page and item pages over HTTP, or HTTPS with `--tls-cert` and `--tls-key`. Page requests read the store, so a new item shows on the next load. Open item Q7 at `/q7.html`. `--host` may be repeated. `--port` defaults to 8765. See [serving security](../how-to/serve-the-page.md#security-loopback-by-default) for the default bind and browser access rules. |
| `service` | Prints a systemd user unit with the supplied serving options, or writes it to `${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service` with `--write`. |
| `url` | Prints the address the installed unit serves, or the default address when no unit is installed. |

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

### Workers endpoint

`GET /workers.json` (under any `base_path`) returns a fresh fleet reading. The
[snapshot command](../explanation/privacy-and-architecture.md#what-quarterdeck-reads-and-writes)
runs with `FM_HOME` set to the configured home. Other inherited `FM_` variables
are removed. The endpoint returns 404 when `firstmate_home` is absent. It
returns 502 immediately if the command is already running. It also returns 502
if the command fails, exceeds 60 seconds, or returns invalid snapshot data.

| Field in `workers.json` | Meaning |
| --- | --- |
| `observed_at` | UTC time the snapshot was taken. |
| `workers` | Ordinary tasks whose terminals are open, one row per ID. Each row has `name`, `project`, `activity`, `status`, `active_at` and `links`. |
| `second_mates` | Second mates reported in the snapshot, one row per ID. Each row has the same fields, with `contact_at` in place of `active_at`. |

For duplicate IDs, the first worker with an open terminal or the first second
mate with a valid endpoint wins within its group. A selected second mate takes
precedence over an ordinary worker with the same ID. An unresolved terminal
state returns 502 if no eligible record resolves it.

`project` uses the project directory's basename or the backlog repository.
`activity` uses the backlog title. `active_at` is calculated from the most
recent reported event's age. `contact_at` uses `observed_at` when the snapshot
confirms contact. Row timestamps are in UTC. The project and row timestamps
can be `null`. `links` is an array of `{text, href}` objects. See the
[status meanings](../how-to/read-workers.md#what-the-statuses-mean).

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
| `firstmate_home` | absolute path after expanding `~` | unset | Local destination for answers submitted to `serve`, and the home whose workers the page lists. |

When `firstmate_home` is absent, `serve` uses a legacy `homes` list only for
answer delivery and only if it contains exactly one object with a valid
`path`. Relative legacy paths are resolved from the config file's directory.
Without a destination, Quarterdeck saves answers with delivery pending.
`captain_waits` is ignored.
Any other key is an error. See
[`quarterdeck.example.json`](../../quarterdeck.example.json).
