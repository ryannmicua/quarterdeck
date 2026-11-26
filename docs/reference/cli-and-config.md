# CLI and configuration reference

## Commands

```text
quarterdeck add --kind resource|list|questions --title TEXT --description TEXT
                [--url URL --link-text TEXT]... [--body-file PATH|-]
                [--project NAME] [--tag TAG ...]
quarterdeck edit ID [--title TEXT] [--description TEXT] [--link-text TEXT --url URL]...
quarterdeck list
quarterdeck show ID
quarterdeck reply ID[.PART] --ask ID --text TEXT [--link-text TEXT --url URL]... [--image PATH --alt TEXT]...
                   [--clarify ID.PART=TEXT]... [--option-changed ID.PART]...
quarterdeck post ID --text TEXT [--link-text TEXT --url URL]... [--image PATH --alt TEXT]...
quarterdeck converse --title TITLE --text TEXT [--config FILE] [--link-text TEXT --url URL]... [--image PATH --alt TEXT]...
quarterdeck mark ID[.PART] answered --via WHERE
quarterdeck mark ID[.PART] closed|open
quarterdeck classify ID[.PART] [--project NAME] [--tag TAG ...] [--kind KIND]
quarterdeck stats [--since DATE] [--json]
quarterdeck catch-up add --title TEXT --summary TEXT [--url URL --link-text TEXT] [--item ID] [--project NAME]
quarterdeck catch-up list
quarterdeck catch-up read ID... | --all
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

See [Reply from the command line](../how-to/reply-from-the-command-line.md) for
store selection and [the setup for `post` and `converse`](../how-to/reply-from-the-command-line.md#post-an-update-or-open-a-conversation).
The attachment options on `post` and `converse` follow the same pairing rules
as `reply`.

| Command | What it does |
| --- | --- |
| `edit` | Changes a resource or question item's title, description or, for a resource, links, in place. Lists and conversations are refused. The ID, saved date, answers and messages stay. Giving `--link-text` and `--url` replaces all of a resource's links, so repeat the pair for each link to keep. Prints `Edited ID`. See [edit a saved item](../how-to/save-items.md#edit-a-saved-item). |
| `add` | Saves a new item and prints its ID. A `resource` needs `--url` and `--link-text`; repeat the pair for more links. A `list` or `questions` item needs `--body-file`. See [the input formats and examples](../how-to/save-items.md#add-an-item) and [how to classify items and questions when saving](../how-to/classify-items.md#classify-when-you-save). |
| `list` | Prints saved items, newest first, with ID, kind, saved date and title. |
| `show` | Prints the item's text, numbering list entries and questions as `Q7.1`, `Q7.2`. Includes question links, stored states, and saved answers with their timestamps and delivery status. For a standalone conversation, prints the transcript in saved order with timestamps, IDs, delivery status for outgoing messages, and attachments. A standalone Firstmate message is marked `Update`. Image references are paths relative to the served site or the item store directory. |
| `reply` | Adds Firstmate's reply to an existing ask on a whole question item (`Q7`) or one question (`Q7.1`), or to an owner's message in a [conversation](../how-to/talk-to-firstmate.md) (`Q12`). It preserves saved answers and question states. Supply the required `--ask ID` from the inbox note and the reply in `--text`. Pair `--link-text` with `--url` and `--image` with `--alt` in the order given. See [Reply from the command line](../how-to/reply-from-the-command-line.md). |
| `post` | Adds a standalone update from Firstmate. Accepts a conversation item ID such as `Q12` and prints `Q12 Updated`. |
| `converse` | Opens a conversation with a title and an update from Firstmate, then prints the new item ID. See [`firstmate_home`](#json-configuration) for home selection. |
| `mark` | Sets question state, optionally for a whole questions item. `--via` is required with `answered` and accepted only with that state. Skips exit with status 0. See [Read answers](../how-to/read-answers.md) for which questions are marked. |
| `classify` | Requires at least one classification option. Use `--project` for an item ID, `--kind` for a question ID, and `--tag` for either. See [how to change classification](../how-to/classify-items.md#change-it-later). |
| `stats` | Prints a question activity table by default or JSON with `--json`. See [Read the report](../how-to/read-the-report.md) for date filtering and field definitions. |
| `catch-up` | `catch-up add` prints the new notice ID, such as `C4`. `catch-up list` prints the ID, UTC saved date and title of each unread notice, newest first. `catch-up read` accepts notice IDs in either case or `--all` for every unread notice. It prints a line such as `C4 Read` once per notice, even if the ID was supplied more than once. If any supplied ID is missing from the store, the command leaves all notices unchanged. `--all` skips the store write when no notices are unread. See [Catch up on finished work](../how-to/catch-up.md). |
| `remove` | Deletes an item. Its ID is never used again. See [image removal](item-store.md#store-layout). |

### Pages

| Command | What it does |
| --- | --- |
| `render` | Writes `index.html` (the Desk) and one `q<N>.html` page per resource, list or question item. Standalone conversations are served only. `--output PATH` names the Desk page and puts the item pages beside it. Otherwise the pages go to `output_dir`. The names `q<N>.html` and `images` are reserved regardless of capitalization. The live state directory also reserves `reply-images` regardless of capitalization. Existing destination pages must carry Quarterdeck's generated-page marker. Render retires obsolete marked item pages. Separately named Desk pages are kept and share the item pages in that directory. The output must be outside the Quarterdeck checkout. See [rendered images](#rendered-images). |
| `serve` | Serves the Desk and item pages over HTTP, or HTTPS with `--tls-cert` and `--tls-key`. Page requests read the store, so a new item shows on the next load. Open item Q7 at `/q7.html`. `--host` may be repeated. `--port` defaults to 8765. See [serving security](../how-to/serve-the-page.md#security-loopback-by-default) for the default bind and browser access rules. |
| `service` | Prints a systemd user unit with the supplied serving options, or writes it to `${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service` with `--write`. |
| `url` | Prints the address the installed unit serves, or the default address when no unit is installed. |

See [how to find references and answered questions](../how-to/use-the-library.md)
on served and rendered pages.

Rendered item pages include current saved answers with their timestamps and
delivery status. Earlier answers, conversation text, clarifications, reply
links, reply images, and Replied badges are exclusive to the served site,
along with Catch-up notices and answer controls.

If `XDG_STATE_HOME` is set when you run `service`, the generated unit includes
that setting so it uses the same [item store](item-store.md) and
[visit log](#visit-log).

`--base-path /qd` (or `base_path` in the config) serves pages and their images
under that prefix for a reverse proxy. A request for the bare prefix is redirected (308)
to `/qd/`, and a path outside the prefix to the same path beneath it, such as
`/q3.html` to `/qd/q3.html`. `--base-path /` turns a configured prefix off.

`serve` still accepts `--allow-marks`, `--watch-waits`, `--no-snapshot` and
`--home`, which older service units pass, and ignores them with a notice.

### Visit log

`serve` appends one line to
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/visits.jsonl` each time it
serves the Desk (`/` or `/index.html` under the base path). Each JSON line
holds the UTC time with milliseconds (`ts`), the `page` value `main`, and
`device`, set to `phone` for a phone browser or `desktop` otherwise, judged
from the `User-Agent`. The record holds no address, item or content. Item
pages, images, the discussion endpoint, and the Workers page and its JSON
endpoints are outside the visit log. Quarterdeck has no login, so every Desk
load counts, whoever makes it.
Logging needs no flag.
Quarterdeck keeps existing records without automatic pruning. It rejects
symbolic links, requires a regular file and sets the file permissions to
`0600` before appending. If a visit cannot be logged, the Desk is still served
and `serve` prints the reason to standard error.

### Rendered images

Pages link to `images/<id>/<file>` relative to themselves. When rendering
into the current store's state directory, pages use its saved images. When
rendering elsewhere, Quarterdeck copies images beside the pages, reuses
identical files already there, and refuses to overwrite other files at
those paths. A missing or unreadable question or option image stops the
render before it writes pages. Keep the `images` folder with the exported
pages. The state directory's `reply-images` tree is reserved for the live
site, and `render` refuses output inside it.

Exports in a separate directory keep image copies of removed items until you
delete `<output>/images/<id>/` by hand. A refused render can leave new image
copies in the output folder.

Rendering into another store's state directory is unsupported. It can replace
or retire that store's generated pages and add image files. If the export
reuses that store's images, the store's `remove` command can break the export.
See the [image removal rules](item-store.md#store-layout).

### Answer endpoint

`POST /answers` (under any `base_path`) takes a numbered `part`, `answer`
text and the card's `revision`. Once the question has an option-change
reply, the request also needs the latest such reply ID as `option_revision`.
A missing or stale option revision returns 409 with a JSON `clarification`
field containing the card's updated clarification HTML. A successful response
includes `earlier_answers_html` for the card's answer history.

### Ask endpoint

`POST /asks` (under any `base_path`) requires an explicit `firstmate_home`
and the same origin as `/answers`. Question-card asks can be saved as
**Not delivered** while the home directory is unavailable. Send
`{"part": "Q7.1", "id": "<ask id>", "text": "...", "home": "<home binding>"}`
to save and deliver an ask. Use `"part": "Q7"` for the whole question item or
`"part": "Q7.1"` for one question. The page generates the 32-character lowercase
hexadecimal ID and takes the resolved home binding from the served form.
Repeating the same create request with the same home and text reuses the
existing ask without attempting delivery again. Add
`"action": "retry"` to the original request to recover from an interrupted
send. Quarterdeck saves the ask if that request never arrived. For a
saved ask, the page can also send
`{"part": "Q7.1", "id": "<ask id>", "action": "retry"}`. Both retry forms
reuse the ask and note IDs when the ask is already saved. A successful response
contains `thread` (rendered conversation HTML) and `message` (delivery status).
For a whole-set ask it also includes `delivery` (`pending` or `sent`) and the
`clarifications` map from the [discussion endpoint](#discussion-endpoint).
Sending or retrying delivery of a whole-item ask
requires at least one question whose state is not Closed. A changed home,
closed question, or removed item can return 409 with a JSON `message`
and a `saved` flag indicating whether the matching ask remains in the store.
The inbox note starts with
`ASK Q7 <ask id>` for the whole item or `ASK Q7.1 <ask id>` for one question,
followed by the ask text.

### Discussion endpoint

`GET /q<N>.discussion.json` (under any `base_path`) returns `thread`
(rendered set discussion HTML) and `clarifications` (a map from full
question IDs to clarification HTML for questions with clarifications). It
serves question items and leaves reply read status intact.

### Conversation endpoint

`POST /conversations` (under any `base_path`) requires an explicit
`firstmate_home` that resolves to an existing directory and the same origin
as `/answers`. If the directory is unavailable, the request returns 403.
Send
`{"id": "<message id>", "title": "...", "text": "...", "home": "<home binding>"}`
to save a new conversation item and deliver its first message. The title is the
topic, up to 200 characters. The page generates the 32-character lowercase
hexadecimal ID. Repeating the same request reuses the saved conversation
instead of creating another, and retries delivery of the first message while it
is pending. The same ID with a different topic or text returns 409. A successful
response contains `message` (delivery status) and `href` (the relative URL of
the conversation card).

On an existing conversation, the owner's messages use the
[ask endpoint](#ask-endpoint) with `"part": "Q12"`, including their first
message in a conversation opened with `converse`.
The directory requirement applies to both sends and retries. If the
conversation is [bound to a home](item-store.md#conversations), a different
resolved path results in 409 before saving or delivering the message.
The inbox note for each message sent by the owner is
`CONVERSATION Q12 <message id> Topic: <topic>`, a line break, then the message
text. See [Reply in a conversation](../how-to/reply-from-the-command-line.md#reply-in-a-conversation).

### Reply acknowledgment endpoint

`POST /replies/read` (under any `base_path`) uses the same origin check as
`/answers`. The served page sends `{"part": "Q7.1", "ids": ["<reply id>"]}`
when the user opens that question's **Messages**, or sends `"part": "Q7"`
when the user opens the whole-item **Messages**. On a standalone conversation
card, the browser sends `"part": "Q12"` and the displayed unread Firstmate
message IDs on open and after a send or retry refresh. A GET alone leaves
read records intact. Every ID must identify a Firstmate message in the named
thread. The response field `replies` maps each Firstmate message ID in that
thread to its current read status (`true` for read).

### Catch-up endpoint

`POST /catch-ups/read` (under any `base_path`) uses the same origin check as
`/answers`. It works without a Firstmate home. The request uses
`Content-Type: application/json` and a single `ids` field, such as
`{"ids": ["C4"]}`. The field must contain a nonempty array of unique
[canonical notice IDs](item-store.md#catch-up-notices). Duplicate IDs return
HTTP 400. If any ID is missing from the store, the endpoint returns HTTP 409.
Both errors leave the store unchanged. On success, the JSON response field
`read` lists the acknowledged IDs in request order. See
[notice retention](../how-to/catch-up.md#how-long-a-notice-stays).

### Workers endpoint

All Workers routes follow the configured `base_path`. `GET /workers.html`
serves the Workers page. When `firstmate_home` is set, the page reads
`GET /workers-saved.json` and calls `GET /workers.json?reuse=1` if that lookup
succeeds and reports a stale reading. **Check workers** calls
`GET /workers.json` when the page has no request in progress.

`GET /workers-saved.json` returns `{"snapshot": ..., "stale": ...}` without
reading the home. `snapshot` is the last reading from `workers.json` in the
same format, or `null` when the server has none. The server keeps the reading
in memory until `serve` stops and returns it even when it is stale. `stale` is
`true` when there is no snapshot or more than one hour has elapsed since the
last successful check. The server uses `CLOCK_BOOTTIME` where available, which
includes time spent suspended, and falls back to `time.monotonic()` elsewhere.
The page displays the fleet's
`observed_at` timestamp. The endpoint returns 404 when `firstmate_home` is absent.

`GET /workers.json` runs and saves a new reading.
With the exact query `?reuse=1`, it shares the result of a check already in
progress or reuses a fresh saved reading. Otherwise it starts a new check.
A failed read keeps the previous reading. The
[snapshot command](../explanation/privacy-and-architecture.md#what-quarterdeck-reads-and-writes)
runs with `FM_HOME` set to the configured home. Other inherited `FM_` variables
are removed. The endpoint returns 404 when `firstmate_home` is absent.
Requests without the exact reuse query return 502 immediately if a check is
already in progress. The endpoint also returns 502 if the command fails,
exceeds 60 seconds, or returns invalid snapshot data.

| Field in a snapshot | Meaning |
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

The config is optional. `converse`, `render`, `serve` and `service` read `--config FILE`,
or `${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json` when it exists.
`url` uses the installed unit's config path or the default file to look up
`base_path` when the unit supplies no `--base-path`.
`catch-up add` and `catch-up read` use the default file, if present, on each
invocation. `catch-up list` reads only the store. Commands that load the
config validate the entire file, including `catch_up_days` when present.
`serve` loads its selected file at startup and uses those settings until it
restarts. When the selected path is a symlink, `serve` resolves it before
reading the file.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `page_title` | string | `Quarterdeck` | The site name in the navigation bar and browser title. `--title` overrides it for `render` and `serve`. |
| `output_dir` | string path | `${XDG_STATE_HOME:-~/.local/state}/quarterdeck` | Where `render` writes the pages. |
| `base_path` | string | none | The URL prefix described above. |
| `catch_up_days` | whole number from 1 to 3650 | `7` | Retention period for [read Catch-up notices](../how-to/catch-up.md#how-long-a-notice-stays). |
| `firstmate_home` | absolute path after expanding `~` | unset | Local destination for answers, asks and conversation messages submitted to `serve`, and the home whose workers the page lists. When set, `converse` requires it to resolve to an existing directory. See [how the store records the binding](item-store.md#conversations). |

`serve` reads `firstmate_home` at startup. `converse` reads it each time it runs.
Changes to this setting require a server restart before `converse` can open
a conversation the owner can respond to on that server.

When `firstmate_home` is absent, `serve` uses a legacy `homes` list only for
answer delivery and only if it contains exactly one object with a valid
`path`. Relative legacy paths are resolved from the directory containing the
resolved config file.
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
