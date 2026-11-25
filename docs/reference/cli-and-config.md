# CLI and configuration reference

## Commands

```text
quarterdeck add --kind resource|list|questions --title TEXT --description TEXT
                [--url URL --link-text TEXT] [--body-file PATH|-]
                [--project NAME] [--tag TAG ...]
quarterdeck list
quarterdeck show ID
quarterdeck mark ID[.PART] answered --via WHERE
quarterdeck mark ID[.PART] closed|open
quarterdeck classify ID[.PART] [--project NAME] [--tag TAG ...] [--kind KIND]
quarterdeck stats [--since DATE] [--json]
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
| `add` | Saves a new item and prints its ID. A `resource` needs `--url` and `--link-text`. A `list` or `questions` item needs `--body-file`. `--project` and `--tag` (repeatable, up to three) classify the item; a question may carry its own `kind` and `tags` in the body. See [the input formats and examples](../how-to/save-items.md#add-an-item) and [Classify items](../how-to/classify-items.md). |
| `list` | Prints saved items, newest first, with ID, kind, saved date and title. |
| `show` | Prints the item's text, numbering list entries and questions as `Q7.1`, `Q7.2`. Each question link is printed with its text and URL. |
| `mark` | Sets the state of one question or every question in a questions item. `answered` records an off-Desk answer with one of the [`--via` sources](#classification). It updates open questions, reports skipped answered or closed questions, and exits with status 0 for skips. All state changes keep any saved Desk answer, its delivery record, and answer history. See [Read answers](../how-to/read-answers.md) for examples. |
| `classify` | Sets or replaces the project and tags of an item (`Q7`), or the kind and tags of one question (`Q7.3`), without touching its content or answers. Options you leave out stay as they are; `--tag` replaces every tag already there. A kind is for a question and a project is for an item, so each is refused on the other. See [Classify items](../how-to/classify-items.md). |
| `stats` | Counts questions by kind, tag and project. `--since DATE` includes saves and dated answers on or after that UTC date. `--json` prints the numbers as JSON. See [Read the report](../how-to/read-the-report.md) for the columns and the JSON fields. |
| `remove` | Deletes an item. Its ID is never used again. See [image removal](item-store.md#store-layout). |

### Pages

| Command | What it does |
| --- | --- |
| `render` | Writes `index.html` (Saved) and one `q<N>.html` page per item. `--output PATH` names the Saved page and puts the item pages beside it. Otherwise the pages go to `output_dir`. The names `q<N>.html` and `images` are reserved regardless of capitalization. Existing destination pages must carry Quarterdeck's generated-page marker. Render retires obsolete marked item pages. Separately named Saved pages are kept and share the item pages in that directory. The output must be outside the Quarterdeck checkout. See [rendered images](#rendered-images). |
| `serve` | Serves the Saved page and item pages over HTTP, or HTTPS with `--tls-cert` and `--tls-key`. Page requests read the store, so a new item shows on the next load. Open item Q7 at `/q7.html`. `--host` may be repeated. `--port` defaults to 8765. See [serving security](../how-to/serve-the-page.md#security-loopback-by-default) for the default bind and browser access rules. |
| `service` | Prints a systemd user unit with the supplied serving options, or writes it to `${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service` with `--write`. |
| `url` | Prints the address the installed unit serves, or the default address when no unit is installed. |

Rendered item pages include any saved answers and their timestamps and
delivery status. Answer controls are available on served pages.

If `XDG_STATE_HOME` is set when you run `service`, the generated unit includes
that setting so it reads the same [item store](item-store.md).

`--base-path /qd` (or `base_path` in the config) serves pages and their images
under that prefix for a reverse proxy. A request for the bare prefix is redirected (308)
to `/qd/`, and a path outside the prefix to the same path beneath it, such as
`/q3.html` to `/qd/q3.html`. `--base-path /` turns a configured prefix off.

`serve` still accepts `--allow-marks`, `--watch-waits`, `--no-snapshot` and
`--home`, which older service units pass, and ignores them with a notice.

### Rendered images

Pages link to `images/<id>/<file>` relative to themselves. When rendering
into the current store's state directory, pages use its saved images. When
rendering elsewhere, Quarterdeck copies images beside the pages, reuses
identical files already there, and refuses to overwrite other files at
those paths. A missing or unreadable saved image stops the render before it
writes pages. Keep the `images` folder with the exported pages.

Exports in a separate directory keep image copies of removed items until you
delete `<output>/images/<id>/` by hand. A refused render can leave new image
copies in the output folder.

Rendering into another store's state directory is unsupported. It can replace
or retire that store's generated pages and add image files. If the export
reuses that store's images, the store's `remove` command can break the export.
See the [image removal rules](item-store.md#store-layout).

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

## Classification

| Field | Allowed values | Accepted by |
| --- | --- | --- |
| `kind` | `pick`, `confirm`, `rule`, `wording`, `scope`, `approve`, `inform` | a question's `kind` |
| `tags` | `product-design`, `ui`, `architecture`, `business-rule`, `terminology`, `process`, `bug`, `security-privacy`, `release`, `research` | an item's or a question's `tags` |
| `answered_via` | `chat`, `review-page`, `elsewhere`, `superseded` | `mark --via` |

Use the exact lowercase spelling shown in these lists. A validation error
names the allowed values. Quarterdeck records answers submitted from the page
with the source `desk`.
