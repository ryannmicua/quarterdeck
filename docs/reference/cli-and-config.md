# CLI and configuration reference

## Commands

```text
quarterdeck add --kind resource|list|questions --title TEXT --description TEXT
                [--url URL --link-text TEXT]... [--body-file PATH|-]
                [--project NAME] [--tag TAG ...]
quarterdeck edit ID [--title TEXT] [--description TEXT] [--link-text TEXT --url URL]...
quarterdeck list
quarterdeck show ID
quarterdeck reply ID[.PART] --ask ID --text TEXT [--for-review] [--link-text TEXT --url URL]... [--image PATH --alt TEXT]...
                   [--clarify ID.PART=TEXT]... [--option-changed ID.PART]...
quarterdeck post ID --text TEXT [--for-review] [--link-text TEXT --url URL]... [--image PATH --alt TEXT]...
quarterdeck converse --title TITLE --text TEXT [--for-review] [--config FILE] [--project ID [--topic ID]]
                     [--link-text TEXT --url URL]... [--image PATH --alt TEXT]...
quarterdeck task review ID --text TEXT [--url URL [--link-text TEXT]]
quarterdeck resolve ID
quarterdeck reopen ID
quarterdeck archive ID
quarterdeck unarchive ID
quarterdeck projects
quarterdeck move ID (--project ID [--topic ID] | --ungrouped)
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
| `show` | Prints the item's text, numbering list entries and questions as `Q7.1`, `Q7.2`. Includes question links, stored states, and saved answers with their timestamps and delivery status. For a standalone conversation, prints the transcript in saved order with timestamps, IDs, delivery status for outgoing messages, and attachments. A standalone Firstmate message is marked `Update`. A task also prints its state, its related conversation while that conversation exists, its result when In review, and its Description. Image references are paths relative to the served site or the item store directory. |
| `reply` | Adds Firstmate's reply to an existing ask on a whole question item (`Q7`) or one question (`Q7.1`), or to an owner's message in a [conversation](../how-to/talk-to-firstmate.md) (`Q12`). It preserves saved answers and question states. Supply the required `--ask ID` from the inbox note and the reply in `--text`. Pair `--link-text` with `--url` and `--image` with `--alt` in the order given. On a conversation, `--for-review` [marks the reply for the owner's review](../how-to/reply-from-the-command-line.md#mark-a-message-for-review); on a question item it is refused. See [Reply from the command line](../how-to/reply-from-the-command-line.md). |
| `post` | Adds a standalone update from Firstmate. Accepts a conversation item ID such as `Q12` and prints `Q12 Updated`. `--for-review` [marks the update for the owner's review](../how-to/reply-from-the-command-line.md#mark-a-message-for-review), so the conversation shows in **For review** with **Asked** until the owner sends a message in it or it is resolved. |
| `converse` | Opens a conversation with a title and an update from Firstmate, then prints the new item ID. `--project` and `--topic` file it in a project and a topic of that project, each by ID such as `P1` or `T2`, in any case. `--topic` needs `--project`, and an unknown ID is refused with the IDs and names that exist. `--for-review` marks the opening update for the owner's review, as it does for `post`. See [`firstmate_home`](#json-configuration) for home selection. |
| `projects` | Prints each project and its topics with their IDs, topics indented under their project, in name order. Prints `No projects.` when the owner has made none. Only the owner creates and renames them, on the Desk. |
| `move` | Files a conversation in a project and optionally a topic, by ID, or returns it to Ungrouped with `--ungrouped`. Give exactly one of `--project` and `--ungrouped`. Prints `Q12 Moved`. Items that are not conversations are refused. It changes only the conversation's [`placement`](item-store.md#conversations). |
| `task review` | Puts a task in review and prints `Q12 In review`. It posts `--text` as a Firstmate update, so the task shows **Unread reply**, and with `--url` it keeps that address as the task's result. `--link-text` names the link and reads Result when left out. The task then shows in **For review**, and its page has **Approve** and **Changes needed**. A conversation that is not a task, a draft and a resolved task are refused. Resolve a finished task with `resolve`. See [create a task](../how-to/create-a-task.md#review-a-task). |
| `resolve`, `reopen` | Marks a conversation resolved or not resolved and prints `Q12 Resolved` or `Q12 Reopened`. Only a conversation item is accepted. Messages and updates leave the mark as it is. See [resolve a conversation](../how-to/talk-to-firstmate.md#resolve-a-conversation). |
| `archive`, `unarchive` | Moves a conversation to the Archived tab or back to the Desk and prints `Q12 Archived` or `Q12 Unarchived`. Only a conversation item is accepted. The resolved and pinned marks stay, and replies still work. See [archive a conversation](../how-to/talk-to-firstmate.md#archive-a-conversation). |
| `mark` | Sets question state, optionally for a whole questions item. `--via` is required with `answered` and accepted only with that state. Skips exit with status 0. See [Read answers](../how-to/read-answers.md) for which questions are marked. |
| `classify` | Requires at least one classification option. Use `--project` for an item ID, `--kind` for a question ID, and `--tag` for either. See [how to change classification](../how-to/classify-items.md#change-it-later). |
| `stats` | Prints a question activity table by default or JSON with `--json`. See [Read the report](../how-to/read-the-report.md) for date filtering and field definitions. |
| `catch-up` | `catch-up add` prints the new notice ID, such as `C4`. `catch-up list` prints the ID, UTC saved date and title of each unread notice, newest first. `catch-up read` accepts notice IDs in either case or `--all` for every unread notice in the initial snapshot. It prints and flushes the notice details before taking the store's write lock, then acknowledges the printed IDs and prints a line such as `C4 Read` once per notice. If any requested ID is missing when the command reads or acknowledges the batch, the command leaves the notices unchanged. `--all` skips the store write when no notices are unread. See [Catch up on finished work](../how-to/catch-up.md). |
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
links, reply images, and Unread reply badges are exclusive to the served site.
Answer controls, Watch and Catch-up notices are available on served pages.

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
pages, images, Watch, the discussion endpoint, and the Workers page and its
JSON endpoints are outside the visit log. Quarterdeck has no login, so every Desk
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
conversation title, up to 200 characters. Add `"project": "P1"` to file it in a
project, and `"topic": "T2"` as well to file it in a topic of that project.
Unknown IDs, a topic outside the project, and a topic without a project return
400 and save nothing. A repeated request ignores both fields, so a conversation
the owner has since moved stays where it is. The page generates the 32-character lowercase
hexadecimal ID. Repeating the same request reuses the saved conversation
instead of creating another, and retries delivery of the first message while it
is pending. The same ID with a different title or text returns 409. A successful
response contains `message` (delivery status), `href` (the relative URL of
the conversation card), `row` (the conversation row HTML), `hold` (the Hold
result row HTML), and `attention`
(the started conversation’s For review row HTML, or `null` if it does not wait for the owner).
The browser shows that conversation's rows at once, derives the tab counts from the
lists, and then reads the lists again from `index.html?lists=1`. That request
returns the Desk page and is not written to the [visit log](#visit-log).

An open, visible Desk also asks `index.html?version=1` about every ten seconds, and
once when the tab becomes visible again. The answer is `{"version": "..."}`, a short
marker of what the Desk lists. The Desk reads its lists again only when the marker
differs, and never while the page is hidden or the pointer is over a row. This
request is not written to the visit log either. The open item in the reading pane
loads again when it has a new message, unless text is typed in it.

On an existing conversation, the owner's messages use the
[ask endpoint](#ask-endpoint) with `"part": "Q12"`, including their first
message in a conversation opened with `converse`.
The directory requirement applies to both sends and retries. If the
conversation is [bound to a home](item-store.md#conversations), a different
resolved path results in 409 before saving or delivering the message.
The inbox note for each message sent by the owner is
`CONVERSATION Q12 <message id> Topic: <conversation title>`, a line break, then the message
text. The note keeps the label `Topic` so existing readers of the inbox keep working. See [Reply in a conversation](../how-to/reply-from-the-command-line.md#reply-in-a-conversation).

### Task endpoints

`POST /tasks` and `POST /reviews` (under any `base_path`) use the same origin
check as `/answers`. Starting a task and sending a note need a `firstmate_home`
that resolves to an existing directory; saving a draft and approving do not. Without it, those
requests return 403.

To create a task, send `{"id": "<message id>", "title": "...", "description": "...", "action": "start", "home": "<home binding>"}`
with `"action": "draft"` to save a draft instead. Add `"source": "Q12"` to relate the task to that conversation,
and `"project"` and `"topic"` as for [a conversation](#conversation-endpoint). A task with a `source` and no
`project` or `topic` is filed where its source is. The title is up to 200 characters.
A started task is saved with one message, `Task started.`, delivered like any message. Its inbox note is
`CONVERSATION Q12 <message id> Topic: <title>`, a line break, `Task started.`, a blank line, `Description:` and the
description. A draft has no message and sends no note. Repeating a start or a draft with the same `id` reuses the saved task,
and the same `id` with a different title or description returns 409.

To start a draft, send `{"part": "Q12", "id": "<message id>", "action": "start", "home": "<home binding>"}`.
A task that is not a draft returns 409, and a repeat with the same `id` delivers the saved message once.

To decide a review, send `{"part": "Q12", "decision": "approve"}`, which resolves the task and returns it to Started
without a result, or `{"part": "Q12", "decision": "changes", "id": "<message id>", "note": "...", "home": "<home binding>"}`,
which sends `Changes needed.`, a blank line and the note as a message and returns the task to Started. A task that is not In review returns 409.

Each response carries `message`, `href`, `row`, `hold` and `attention` as the
[conversation endpoint](#conversation-endpoint) does.

### Pin endpoint

`POST /pins` (under any `base_path`) uses the same origin check as `/answers`
and needs no `firstmate_home`. Send `{"part": "Q12", "pinned": true}` to pin a
conversation or `"pinned": false` to unpin it. Only a standalone conversation
can be pinned, and any other ID returns 400. The request changes the
conversation's [`pinned` field](item-store.md#conversations) and nothing else.
It adds no message, leaves unread replies unread and keeps the conversation's
latest activity. The response is `{"pinned": true}` or `{"pinned": false}`.

### Later endpoint

`POST /laters` (under any `base_path`) uses the same origin check as `/pins`
and needs no `firstmate_home`. Send `{"part": "Q7", "later": true}` to keep a
question item or a conversation for later, or `"later": false` to take it out.
Any other item, ID or field returns 400. Keeping an item for later after it
no longer waits for the owner returns 409. The request changes the item's
[`later` field](item-store.md#later) and nothing else. The response is
`{"later": true}` or `{"later": false}`.

### Resolution and archive endpoints

`POST /resolutions` and `POST /archives` (under any `base_path`) use the same
origin check as `/pins` and need no `firstmate_home`. Send
`{"part": "Q12", "resolved": true}` to `/resolutions` or
`{"part": "Q12", "archived": true}` to `/archives`, and `false` to clear the
mark. Only a standalone conversation is accepted, and any other ID or field
returns 400. Each request changes the conversation's
[`resolved` or `archived` field](item-store.md#conversations) and nothing else.
It adds no message, keeps the other marks and never refuses a later message.
The response repeats the field, such as `{"resolved": true}`, and carries
`hold`, the conversation's row for the Hold list, which is empty when the
conversation is archived.

### Project endpoint

`POST /groups` (under any `base_path`) uses the same origin check as `/answers`
and needs no `firstmate_home`. It takes one of three bodies. `{"name": "Field Atlas"}`
creates a project. `{"project": "P1", "name": "Wayfinding"}` creates a topic in
that project. `{"id": "P1", "name": "Atlas"}` renames the project or topic with that
ID, which keeps its ID and every conversation filed in it. A name is up to 60
characters, trimmed, on one line. A name that another project (or another
topic in the same project) already has, ignoring case, returns 409 with a JSON
`message`. Anything else that does not fit returns 400. The response is
`{"id": "P1", "projects": [...]}`, with the new or renamed ID and every project and its
topics in name order.

### Move endpoint

`POST /moves` (under any `base_path`) uses the same origin check and needs no
`firstmate_home`. Send `{"part": "Q12", "project": "P1", "topic": "T2"}`, with
`"topic": null` for a project only, or both `null` to return the conversation to
Ungrouped. Only a conversation can be moved, and an unknown ID, a topic outside
the project, or a topic without a project returns 400. The request changes the
conversation's [`placement`](item-store.md#conversations) and nothing else. It adds no
message, keeps the pin and the unread reply, and leaves the latest activity alone.
The response repeats `project` and `topic`.

### Watch page

`GET /watch.html` (under any `base_path`) serves the read-only
[Watch page](../how-to/watch-firstmate.md). When `firstmate_home` is set,
**Current work** reads `GET /workers-saved.json`. Reading that endpoint
leaves the fleet snapshot untouched.

### Reply acknowledgment endpoint

`POST /replies/read` (under any `base_path`) uses the same origin check as
`/answers`. The served page sends `{"part": "Q7.1", "ids": ["<reply id>"]}`
when the user opens a notice for that reply. A reply in a whole-item
discussion or standalone conversation uses the item ID as `part`. Explicitly
opening **Messages**, or the page opening a collapsed **Messages** on load to
show its first unread reply, acknowledges all unread replies it shows. Sending,
retrying, and a GET alone leave read records intact. Every ID must identify a
Firstmate message in the named thread. The response field `replies` maps each Firstmate message ID in that
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
`GET /workers-saved.json` and shows that reading at once, even when it is
stale. It calls `GET /workers.json?reuse=1` only when the server has no
reading. **Check workers** calls `GET /workers.json` when the page has no
request in progress. If a background check is running, that request waits for
it and returns its result.

While `serve` runs with `firstmate_home` set, it reads the home when it starts
and then every 30 minutes. The interval is fixed. The last good reading is
kept in `workers-saved.json` in the same directory as the item store, with
mode `0600`, so a restart shows it at once. The file records the home it was
read from, and a restart with a different `firstmate_home` ignores it. A background check
that fails or finds a check already running keeps that reading.

`GET /workers-saved.json` returns
`{"snapshot": ..., "stale": ..., "failed": ...}` without reading the home.
`snapshot` is the last reading from `workers.json` in the same format, or
`null` when the server has none. The server returns it even when it is stale.
`stale` is `true` when there is no snapshot or more than one hour has elapsed
since the last successful check. `failed` is `true` when the most recent check
failed since the server started, and the page then says **Check failed** with
the time of the saved list. The server uses `CLOCK_BOOTTIME` where available,
which includes time spent suspended, and falls back to `time.monotonic()`
elsewhere. A reading loaded from disk ages by the wall-clock time since it was
saved. The server ignores a file it cannot read.
The page displays the fleet's
`observed_at` timestamp. The endpoint returns 404 when `firstmate_home` is absent.

`GET /workers.json` runs and saves a new reading.
With the exact query `?reuse=1`, it shares the result of a check already in
progress or reuses a fresh saved reading. Otherwise it starts a new check.
A failed read keeps the previous reading. The reading also holds a `work` list
that the Desk's [Under way tab](../how-to/see-work-under-way.md) shows. The Desk
renders that tab from the saved reading on every request and never starts a
check. A saved reading without `work`, from an earlier version, is ignored until
the next check. The
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
