# CLI and configuration reference

## Commands

```text
quarterdeck add --kind resource|list|questions --title TEXT --description TEXT
                [--url URL --link-text TEXT] [--body-file PATH|-]
                [--project NAME] [--tag TAG ...]
quarterdeck list
quarterdeck show ID
quarterdeck reply ID[.PART] --ask ID --text TEXT [--link-text TEXT --url URL]... [--image PATH --alt TEXT]...
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
Question-part IDs use an item ID and a positive part number, separated by a
period, as in `Q7.1`. The CLI also accepts `q7.1`. Browser requests use the
uppercase form.

### Items

| Command | What it does |
| --- | --- |
| `add` | Saves a new item and prints its ID. A `resource` needs `--url` and `--link-text`. A `list` or `questions` item needs `--body-file`. See [the input formats and examples](../how-to/save-items.md#add-an-item) and [how to classify items and questions when saving](../how-to/classify-items.md#classify-when-you-save). |
| `list` | Prints saved items, newest first, with ID, kind, saved date and title. |
| `show` | Prints the item's text, numbering list entries and questions as `Q7.1`, `Q7.2`. Includes question links, stored states, and saved answers with their timestamps and delivery status. |
| `reply` | Adds Firstmate's reply to an existing ask on a whole item (`Q7`) or one question (`Q7.1`). Supply the required `--ask ID` from the inbox note and the reply in `--text`. Pair `--link-text` with `--url` and `--image` with `--alt` in the order given. See [Reply from the command line](../how-to/reply-from-the-command-line.md). |
| `mark` | Sets question state, optionally for a whole questions item. `--via` is required with `answered` and accepted only with that state. Skips exit with status 0. See [Read answers](../how-to/read-answers.md) for which questions are marked. |
| `classify` | Requires at least one classification option. Use `--project` for an item ID, `--kind` for a question ID, and `--tag` for either. See [how to change classification](../how-to/classify-items.md#change-it-later). |
| `stats` | Prints a question activity table by default or JSON with `--json`. See [Read the report](../how-to/read-the-report.md) for date filtering and field definitions. |
| `remove` | Deletes an item. Its ID is never used again. See [image removal](item-store.md#store-layout). |

### Pages

| Command | What it does |
| --- | --- |
| `render` | Writes `index.html` (the Desk) and one `q<N>.html` page per item. `--output PATH` names the Desk page and puts the item pages beside it. Otherwise the pages go to `output_dir`. The names `q<N>.html` and `images` are reserved regardless of capitalization. Existing destination pages must carry Quarterdeck's generated-page marker. Render retires obsolete marked item pages. Separately named Desk pages are kept and share the item pages in that directory. The output must be outside the Quarterdeck checkout. See [rendered images](#rendered-images). |
| `serve` | Serves the Desk and item pages over HTTP, or HTTPS with `--tls-cert` and `--tls-key`. Page requests read the store, so a new item shows on the next load. Open item Q7 at `/q7.html`. `--host` may be repeated. `--port` defaults to 8765. See [serving security](../how-to/serve-the-page.md#security-loopback-by-default) for the default bind and browser access rules. |
| `service` | Prints a systemd user unit with the supplied serving options, or writes it to `${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service` with `--write`. |
| `url` | Prints the address the installed unit serves, or the default address when no unit is installed. |

See [how to find references and answered questions](../how-to/use-the-library.md)
on served and rendered pages.

Rendered item pages include any saved answers and their timestamps and
delivery status. Conversation text, reply links, reply images and Replied
badges are exclusive to the served site. Answer controls are available on
served pages.

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
The state directory's `reply-images` folder belongs to the live site.

Exports in a separate directory keep image copies of removed items until you
delete `<output>/images/<id>/` by hand. A refused render can leave new image
copies in the output folder.

Rendering into another store's state directory is unsupported. It can replace
or retire that store's generated pages and add image files. If the export
reuses that store's images, the store's `remove` command can break the export.
See the [image removal rules](item-store.md#store-layout).

### Ask endpoint

`POST /asks` (under any `base_path`) requires the explicit `firstmate_home`
and the same origin as `/answers`. Send
`{"part": "Q7.1", "id": "<ask id>", "text": "...", "home": "<home binding>"}`
to save and deliver an ask. Use `"part": "Q7"` for the whole item or
`"part": "Q7.1"` for one question. The page generates the 32-character lowercase
hexadecimal ID and takes the resolved home binding from the served form.
Repeating the same create request returns the existing ask. Add
`"action": "retry"` to the original request to recover from an interrupted
send. Quarterdeck saves the ask if that request never arrived. For a
saved ask, the page can also send
`{"part": "Q7.1", "id": "<ask id>", "action": "retry"}`. Both forms
reuse the saved ask and note IDs. A changed home, closed question,
or removed item can return 409. The inbox note starts with
`ASK Q7 <ask id>` for the whole item or `ASK Q7.1 <ask id>` for one question,
followed by the ask text.

### Reply acknowledgment endpoint

`POST /replies/read` (under any `base_path`) uses the same origin check as
`/answers`. The served page sends `{"part": "Q7.1", "ids": ["<reply id>"]}`
when the user opens that question's **Messages**, or sends `"part": "Q7"`
when the user opens the whole-item **Messages**. Every ID must belong to
the named conversation. The response field `replies` maps each reply ID in
that conversation to its current read status (`true` for read).
Page loads leave these records intact.

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
| `firstmate_home` | absolute path after expanding `~` | unset | Local destination for answers and asks submitted to `serve`, and the home whose workers the page lists. |

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
| `via` | `chat`, `review-page`, `elsewhere`, `superseded` | `mark --via` |

Use the exact lowercase spelling shown in these lists. A validation error
names the allowed values. Quarterdeck records answers submitted from the page
with the source `desk`.
