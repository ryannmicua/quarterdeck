# Privacy and architecture

Quarterdeck is a small local renderer, not a Firstmate extension. A render can
use one home from `--home` or `FM_HOME`, or several independent homes from the
`homes` array in a config file. For each home it reads the configured data
directory and adds a labeled section to one self-contained page.
`FM_DATA_OVERRIDE` selects a non-default data directory for the legacy
single-home invocation. In a home list, each entry may set its own `data_dir`;
otherwise that home's `data/` directory is used.

The inputs include `data/backlog.md`, `data/<id>/report.md` files and their optional
`report.html` companions, documents
named in [activity cards](../how-to/watch-activity-cards.md#open-the-documents-a-card-names), the optional
`data/captain-board.json` board (see the
[board file reference](../reference/captain-board.md)), and the optional
`data/topics/*.md` topic files Firstmate writes, one conversation per topic (see
the [topic files reference](../reference/topic-files.md)). Generated thread pages sit
beside the main page (see the [CLI reference](../reference/cli-and-config.md) for filenames),
with the topic's text and eligible `data/` documents named in its Links or conversation;
`quarterdeck topics check` reads the same
files and writes nothing. When topics exist, `search.html` carries a search
index of the same topic, task and report titles the other pages show, so search
works without a server. The front page keeps when you last left it in the
browser's `localStorage`; Quarterdeck stores nothing about it. For each
configured parent home, Quarterdeck also reads the optional
`data/secondmates.md` route registry. Each local route's `home:` field points
to another Firstmate home, which gets its own labeled section underneath the
parent. That secondmate has its own `data/backlog.md` and report files. A remote
route is shown as unavailable as a separately loaded home. The parent's
bearings snapshot may still include remote secondmate rows collected through
Firstmate's routes and cache. Quarterdeck does not use SSH or fetch remote
secondmate data itself. This follows Firstmate's [secondmate route and home layout](https://github.com/kunchenguid/firstmate/blob/main/docs/configuration.md).

Quarterdeck reads these files directly. The first exception is a home's own
`bin/fm-bearings-snapshot.sh --json`, run with a short timeout so the page
agrees with Firstmate's `/bearings`; that script may refresh a parent-side
Firstmate cache as part of its own operation. `--no-snapshot` skips it, and the
page falls back to parsing the backlog when it is absent, fails, or times out.
The wrapper may also read registered remote secondmate ledgers through
Firstmate's routes and show the returned rows.

The "Decisions and recent work" section and `quarterdeck attention` read the home's
`state/fleet-ledger.jsonl` directly and run the home's own
`bin/fm-attention.sh --json`, Firstmate's documented read-only feed of open
decisions; `--no-snapshot` skips that script for the page. Both are versioned
formats documented by Firstmate, and Quarterdeck refuses a version it does not
understand rather than guess; see the
[attention JSON reference](../reference/attention-json.md). This keeps the
recent-work view an add-on: Firstmate itself is unchanged. `quarterdeck priorities`
writes only its preference file and refuses destinations inside selected local
homes or their data directories. See the
[CLI and configuration reference](../reference/cli-and-config.md) for its location
and overrides.

Quarterdeck invokes no other Firstmate script directly, except one: a reaction
or a captain-wait discard (below) runs the home's own `bin/fm-inbox.sh note`.
Quarterdeck makes no network calls itself, except phone notifications (below)
once you turn them on. `render` and
`serve` write nothing to a home directly.

The one exception is the explicit `quarterdeck board` command, which Firstmate
runs to keep its captain board current. It writes only
`<data>/captain-board.json`: the file Firstmate already owns and Quarterdeck
already reads, in the schema Quarterdeck owns. It is a schema-checked
replacement for Firstmate editing that JSON by hand, not a new data flow: it
reads nothing but that file, runs no Firstmate script, never runs from `render`
or `serve`, and has no HTTP endpoint. It writes atomically under a lock on the
data directory and refuses to overwrite a board it cannot read.

`quarterdeck serve` adds an HTTP listener. By default it answers only `GET` for
generated pages and `cards.json` (the page's activity cards, built from the same
data), has no write endpoints and no login, and binds 127.0.0.1.
Which cards you have seen stays in your browser's `localStorage`; Quarterdeck stores nothing about it.
Binding another address exposes real work data to that network.

`quarterdeck serve --allow-marks` enables the write endpoint
`POST /api/reports/review`, which marks, unmarks, discards or undiscards one
known report ID. It runs the same code, storage and fingerprint as `quarterdeck
reports mark-reviewed`, `unmark-reviewed`, `discard` and `undiscard`. Each
accepted request writes only the state database in Quarterdeck's state
directory, never a Firstmate home.
A request must include the fingerprint shown on the page, be `application/json`,
come from the page's own origin (cross-origin `Origin` or `Sec-Fetch-Site`
values are refused), and name an existing, unambiguous report whose content has
not changed since the page loaded. The other allowed writes are described below;
all remaining routes and methods stay refused.
There is no key or login: anyone who can open the served page can mark or
unmark or discard reports, so on a non-loopback bind that means anyone on that
network.
The endpoint grants no other write access. Use marks only on a network you
trust, or put the server behind an authenticating TLS reverse proxy.
The static `render` output never contains the buttons.

`serve --allow-marks` also enables `POST /api/views`, which saves one briefing
view (and optionally the startup view) to the preferences file. It applies the
same origin, content-type and no-key rules, validates the view exactly as
`quarterdeck views save` does, and writes only that file, never a home.

`serve --allow-marks` also enables `POST /api/reactions` for
reactions: a quick Approve, Hold, Not now or Question, a reply, or a one-tap
answer taken from the call's own text (sent as a plain reply, through this same
endpoint), on a
desk call, a related item or a report. It applies the same origin, content-type
and no-key rules as the mark endpoint, and accepts only an item that exists. It
is the one place Quarterdeck acts on a home beyond reading: it runs that home's
own `bin/fm-inbox.sh note --request-id qd-<id> --json -- <one line>`, the
captain's out-of-band channel to Firstmate. The line names the item, its task
ID, the reaction, the text and the time; nothing else leaves Quarterdeck, and
Quarterdeck does not write the home's inbox files itself. The note goes to the
configured home the item belongs to (a secondmate's item goes to its parent
home, which routes it). Only after the script succeeds is the reaction recorded
in the state database, so a failed delivery is reported on the card and nothing
is recorded. `quarterdeck reactions import FILE` runs the same delivery for the
reactions recorded in the cloud artifact, skipping ids already delivered.
Unsent reply text stays in the browser; see the
[reply draft rules](../how-to/serve-the-page.md#react-to-calls-and-reports-from-the-page).
The same endpoint takes a question about a topic (`kind` `topic`, the topic's
page name or `+new` for a new topic) and an answer to a topic decision (`kind`
`decision`, `<topic>/<decision id>`), each as text only. The note asks Firstmate
to record it in `data/topics/<topic-id>.md`; Quarterdeck itself never writes the
topic file. A topic, decision or answer that does not exist, or a decision the
file already shows answered or that was already answered from Quarterdeck, is refused before anything is sent.
Each configured home is loaded independently. A missing or unreadable home
shows its own error while other sections still render.

Pages and exports show the conversation selected by the newest operator record
(see [See only this session](../how-to/see-only-this-session.md)). Quarterdeck reads
saved Claude Code messages from that home and uses that conversation's typed
messages and Firstmate's text replies. Parsed messages can be cached in memory.
Eligible chat links are included in the generated output. Credentials and
recognized token and signing parameters are omitted. The transcript files are
read-only.

The captain-wait watcher (`quarterdeck waits sync|watch`, or `serve
--watch-waits`) is opt-in. Besides the backlog it reads the home's saved Claude
Code sessions, by default `~/.claude/projects/<encoded home path>/*.jsonl`,
keeping only the messages the captain typed and Firstmate's text replies, and
only from sessions where the captain typed something. It never writes those
files or the home. To brief a card it runs one configured helper command, by
default a headless Claude Code run with every tool, MCP server, slash command
and saved setting turned off. That run sends the prompt to the same model
service that already runs Firstmate's sessions; Quarterdeck itself still makes
no network calls. The prompt holds the item's task id, title and hold note and
Firstmate's messages around the hold, or the captain's question naming the item
and Firstmate's answer. The helper only prints JSON naming paragraph numbers,
a kind and a summary; Quarterdeck validates it and copies the chosen
paragraphs into its own card store, by default
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/captain-waits/waits.json` (mode
0600), keyed by each home's resolved path so a command reads, shows and
discards only the selected homes' cards. The store holds Firstmate's explanations and the captain's questions
that named an item, so Quarterdeck refuses a store inside its checkout, a
selected home or a home's data directory. Each helper run has a time limit,
runs in the store's `work/` folder, and at most one runs per wait.

`serve --allow-marks` also enables `POST /api/waits/discard` (body `{key,
hold_set}`), and `quarterdeck waits discard` does the same from a terminal. With
the same origin, content-type and no-key rules as reactions, it runs the owning
home's `bin/fm-inbox.sh note --request-id qd-wait-<hash> --json -- <one line>`
asking Firstmate to drop the hold, and only after that succeeds marks the card
hidden in the card store. Discard never changes the backlog; the card is
released when Firstmate drops the hold.

The page may include task names, recommendations, and local paths from every
selected home. Its default location is under
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/`, outside the repo. Keep any
custom output outside the repo and selected homes, and keep real config outside
the repo as well. Quarterdeck refuses output inside its checkout or a selected
home. The page uses inline CSS; report-review requests on served pages stay on
the serving origin. `serve` is an
optional HTTP process that can run under the systemd user unit. The page has
no write-back path into a Firstmate home; the opt-in report marks above write only
Quarterdeck's own state directory.

Board items may link decision briefs published as private Claude artifacts.
Quarterdeck only renders the link and its one-line recommendation; it never
fetches the brief. Brief content lives with the artifact service, which is the
same processor that already runs Firstmate's Claude sessions, never in this
repository.

Quarterdeck writes readable HTML copies of linked reports, reports needing
review, and the backlog beside the main page; the dependency-free Markdown
renderer escapes source HTML. `quarterdeck reports read <id>` uses the same
renderer. Site report pages can instead show a captain-facing HTML companion;
see the [report-view guide](../how-to/watch-activity-cards.md#read-the-report).
The shared report view only reads UTF8 regular report files of 2 MB or less
whose resolved parent is a direct child of the configured data directory;
symlinks escaping the report directory are refused. The
[CLI reference](../reference/cli-and-config.md) documents its output path,
report IDs, and collision handling.

Linked documents and site reports use Quarterdeck's navigation wrapper, which
can access the site's browser storage for the last-look time. For HTML previews
the wrapper loads the unchanged content in a frame with
`sandbox="allow-scripts allow-popups"`, including in rendered snapshots.
Original-file copies use `raw-*.html` or `raw-*.txt` and receive
`Content-Security-Policy: sandbox allow-scripts allow-popups` when served.
These sandboxes isolate source scripts from Quarterdeck's origin, storage and write
endpoints. See the [activity-card guide](../how-to/watch-activity-cards.md#open-the-documents-a-card-names).

Review marks, discards, relevance and relations are stored separately from
rendered pages in a SQLite database at
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/review-state/state.db` (mode
0600). A mark or discard stores the sha256 fingerprint of the report bytes; a
relevance or relation row stores a report ID or `<home-label-slug>/<task-id>`
and a task ID. A `marks.json` from an earlier version is imported once and left
unchanged beside it. Before it reads or writes this state, Quarterdeck verifies
the state directory is outside the repository checkout, all selected homes, and
their configured data directories. The database contains IDs, fingerprints and
short reasons, not a copy of the report or Firstmate data.

The Adoption page reads each home's `data/qd-adoption/tags.jsonl` (topic tags,
never chat content) and, with `serve --allow-marks`, Quarterdeck appends page
visits (time, page, phone or desktop; no address; automatic refreshes excluded) to
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/visits.jsonl`. With that same
flag, page builds append first-observed report and Needs-you arrivals to the
`adoption_events` table in the existing private review-state database. Report
triage actions from the CLI, served page and artifact imports append events in
their state transaction, including clears as `unmarked`. Events contain only
kind, item identifier and time, with no historical backfill or message/card
content. Summary rendering only reads this history. Later edits and state
changes preserve historical weekly totals. The first-seen estimate joins
Needs-you observation times with subsequent visits. See
[see how much you use Quarterdeck](../how-to/see-your-adoption.md).

For notification prerequisites, setup and phone-specific withdrawal behavior, see
[Get phone notifications](../how-to/serve-the-page.md#get-phone-notifications).
Nothing is sent until a browser turns them on from the page. They use standard
Web Push. The page's service worker subscribes with the browser's own
push service, and the subscription endpoint stores that subscription (an
endpoint URL and the browser's public keys) under the same origin, content-type
and no-key rules as the other writes. See the [CLI reference](../reference/cli-and-config.md#command)
for endpoint paths and prefix routing. Only endpoints on the Google, Apple,
Mozilla and Microsoft push services are accepted, so the server never posts to
an address a visitor chose. While subscriptions exist, a timer rebuilds the page
about once a minute even with no browsers open. After each page build Quarterdeck
compares the Needs-you cards with its record and, for each new one, posts one message to each
subscription's push service: an encrypted payload (RFC 8291) signed with
Quarterdeck's own key (RFC 8292). The payload holds the item's title (a call's
title or a topic decision's question; for an answered question, "Firstmate
answered" and the topic's title), a tag and a link to that card; the tag is a
hash of the card's identity. No recommendation, detail or other text is sent.
The push service sees only ciphertext, but the title shows on the phone's lock
screen to anyone holding the phone. Lifecycle tracking follows the behavior in the serving guide.
Transient delivery failures leave the item pending for the next check; once at
least one push service accepts a message, it is not retried for other phones.
The uncached push-key response also lists server-confirmed inactive tags; page-open
cleanup closes only those notifications, never tags merely absent from a cached
page.
Every state file is under
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/` with mode 0600:
`push-vapid.pem` (the signing key), `push-subscriptions.json` and
`push-items.json` (tags and times only). A subscription the push service reports
gone (HTTP 404 or 410) is deleted. Other non-transient client refusals count
toward removal after three failures; a successful delivery resets the count.
Timeouts, rate limits and server failures retain the subscription.
Without the `cryptography` package the feature is off and the rest of
Quarterdeck still uses only the standard library.

`quarterdeck export` is a read of the homes that writes a snapshot of the page's
data (calls, report titles and recommendations, states and fingerprints) to a
file you choose. By default current, unreviewed reports include their Markdown
bodies (`--bodies desk` or `none` narrows this); stale, discarded and reviewed
reports omit theirs. Home metadata paths are
omitted, but report text may contain paths and other work data. It leaves the
machine only if you publish the artifact built from it; `quarterdeck import`
applies the artifact's recorded actions to the local state database and writes
nothing else.

Lavish request controls are present only on `quarterdeck render --lavish`
pages, including each report needing review. A report request carries its
report ID, title, home label, and source path. Controls queue a structured
prompt through Lavish's page API. Quarterdeck does not send the prompt, listen
for requests, poll for a result, or create a Lavish page. An armed listener on
that session must do that work and reply with the page link in the session
conversation panel. Without an armed listener, the request has no effect.

The repository ignores common output folders, image screenshots, and local
configuration files. The staged-file privacy guard rejects generated HTML,
Firstmate data files, likely host-specific paths, private IPs, and pull-request
URLs. The guard is a backstop; examples and tests still need to remain fictional.
