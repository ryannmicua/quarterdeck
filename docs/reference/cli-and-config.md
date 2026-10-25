# CLI and configuration reference

## Command

```text
quarterdeck render [--home PATH] [--output PATH] [--config FILE] [--title TEXT] [--all] [--lavish] [--no-snapshot]
quarterdeck serve [--host ADDRESS ...] [--port N] [--home PATH] [--config FILE] [--title TEXT] [--no-snapshot] [--allow-marks]
quarterdeck service [--write] [--allow-marks] [--host ADDRESS ...] [--port N] [--home PATH] [--config FILE]
quarterdeck url [--json]
quarterdeck reactions import FILE|- [--dry-run] [--home PATH] [--config FILE]
quarterdeck reports list [--home PATH] [--config FILE]
quarterdeck reports read <report-id> [--home PATH] [--config FILE] [--open]
quarterdeck reports mark-reviewed <report-id> [--home PATH] [--config FILE]
quarterdeck reports unmark-reviewed <report-id> [--home PATH] [--config FILE]
quarterdeck reports discard <report-id> [--home PATH] [--config FILE]
quarterdeck reports undiscard <report-id> [--home PATH] [--config FILE]
quarterdeck reports set-relevance <report-id> current|stale [--reason TEXT] [--home PATH] [--config FILE]
quarterdeck reports relate <report-id> --to <task-id> [--remove] [--home PATH] [--config FILE]
quarterdeck calls list [--home PATH] [--config FILE]
quarterdeck calls set-relevance <task-id> current|stale [--reason TEXT] [--home PATH] [--config FILE]
quarterdeck calls relate <task-id> --to <task-id> [--remove] [--home PATH] [--config FILE]
quarterdeck attention --json|--check [--all-decisions] [--project NAME] [--more] [--view [NAME]] [--preferences FILE] [--home PATH] [--config FILE]
quarterdeck priorities show [--json] [--preferences FILE] [--home PATH] [--config FILE]
quarterdeck priorities set PROJECT [PROJECT ...] [--preferences FILE] [--home PATH] [--config FILE]
quarterdeck priorities clear [--preferences FILE] [--home PATH] [--config FILE]
quarterdeck views list|show NAME [--json] [--preferences FILE]
quarterdeck views save NAME --section SECTION [--section SECTION ...] [--default] [--preferences FILE] [--home PATH] [--config FILE]
quarterdeck views default NAME [--preferences FILE] [--home PATH] [--config FILE]
quarterdeck export [--output FILE] [--artifact FILE] [--home PATH] [--config FILE] [--title TEXT] [--bodies all|desk|none] [--no-snapshot]
quarterdeck import <actions.json|-> [--dry-run] [--home PATH] [--config FILE]
quarterdeck board upsert <task-id> [--id ID] [--group GROUP] [--title TEXT] [--ask TEXT] [--detail TEXT] [--topic TEXT] [--project TEXT] [--brief-url URL] [--recommendation TEXT] [--link LABEL=URL ...] [--touch] [--touched-at ISO] [--home PATH] [--data-dir PATH]
quarterdeck board touch <task-id> [--at ISO] [--home PATH] [--data-dir PATH]
quarterdeck board remove <task-id> [--id ID] [--home PATH] [--data-dir PATH]
quarterdeck board active [--new-session] [--home PATH] [--data-dir PATH]
quarterdeck board list [--json] [--home PATH] [--data-dir PATH]
quarterdeck add <firstmate-home> [--label LABEL] [--config FILE]
quarterdeck list [--config FILE]
quarterdeck remove <label-or-path> [--config FILE]
quarterdeck install [--uninstall]
quarterdeck update
quarterdeck help [COMMAND] [--json]
```

Run `quarterdeck --help` or `quarterdeck help` for a command overview.
`quarterdeck <command> --help` describes that command. `quarterdeck help
--json` prints the command index and each command's documentation paths for
tools that need to discover the interface. `quarterdeck install` runs the
user-local installer; add `--uninstall` to remove installer-created files.
`quarterdeck update` runs that same installer path to update the installed
checkout. The documentation tree is included in the checkout and its path is
printed by `quarterdeck help`.

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
under that parent. It may run that home's
`bin/fm-bearings-snapshot.sh --json` for the bearings view and
`bin/fm-attention.sh --json` for the "Waiting on you" section, unless
`--no-snapshot` is set, and it reads `state/fleet-ledger.jsonl` for that
section. Quarterdeck itself makes no network calls and invokes no other
Firstmate script directly, apart from `bin/fm-inbox.sh note` for reactions. The snapshot wrapper may read registered
remote secondmate ledgers through Firstmate's routes and cache, and Quarterdeck
shows the returned rows. A missing or unreadable home in a configured list
gets a failure section;
the other homes still render. The page links to readable HTML copies of
selected reports and backlogs, generated beside the main page, and HTTPS links
for GitHub pull requests.

`priorities set` replaces the saved priorities with one to three project names
recorded in selected homes' route registry project entries or waiting items.
`show` prints them (`--json` includes the limit and file path); `clear` saves an
empty list. Names resolve as described in the
[attention JSON reference](attention-json.md#waiting-and-priorities).

`views` keeps saved briefing views beside the priorities. A view has a name
(up to 40 characters, unique ignoring case) and one or more ordered sections:
`waiting` (Waiting on you), `recent` (the last six hours of merged and started
work), `done` (Done recently, merged work in the last six hours), `priorities` (one section per priority project, in set order),
`project:NAME` (one named project) and `information` (For your information). A
section may appear once per view; unknown section types and unregistered
projects are refused with exit 2. `views save` replaces a view of the same name
(or adds one, up to eight views) and `--default` also makes it the startup view;
`views default NAME` only changes the startup view; `views list` and `show`
print the saved views (`--json` includes the startup view and file path). With
no views saved, Quarterdeck uses two seeded views and writes nothing until you
save: Morning (`waiting`, `recent`, `information`, the startup view) and
Priorities (`priorities`, `waiting`). Saving the first view writes the seeded
views too. See [Briefing views](attention-json.md#briefing-views) for what each
section holds.

The preference file is
`${XDG_CONFIG_HOME:-~/.config}/quarterdeck/preferences.json`.
`priorities` and `views` subcommands and `attention` accept `--preferences FILE` to override
it; page commands read the default file. Its JSON object has `version: 1` and
`priority_projects`, a list of at most three distinct, nonempty strings, plus
optionally `views` (a list of `{name, sections}`, sections being `{type}` or
`{type: "project", project}`) and `default_view` (the name of one saved view);
unknown members are preserved on writes. A missing file means no priorities. An
unreadable or invalid file makes the priority and attention commands exit 2;
the page ignores it and uses no priorities. Writes are atomic with mode `0600`
and refuse destinations inside selected local homes or their data directories.

`attention --json` prints the shared waiting and recent-work view;
`--project NAME` selects a project and `--more` includes held-back waiting items.
`--all-decisions` also fills the background decision list. `attention --check`
validates the Firstmate sources and prints one line. See the
[attention JSON reference](attention-json.md) for field definitions, scoping,
relevance rules, source errors, compatibility windows, and exit statuses.

`reports list`, `reports read`, `reports mark-reviewed`, and
`reports unmark-reviewed` use the selected homes in the config, or the single
home from `--home` or `FM_HOME`. They also accept `--config FILE`. Report IDs
have the exact form `<home-label-slug>/<task-id>`: the label slug is the
case-folded label with each run of characters outside `a` through `z` and `0`
through `9` replaced by `-`, then leading and trailing hyphens removed; the
task ID is the report's immediate parent directory name unchanged. The slug is
uncapped. An empty slug becomes `home`. For example, `Maple Harbor` and `amber-18`
produce `maple-harbor/amber-18`. A collision appears as `ID collision` in the
list. Read and review commands refuse the ambiguous ID with matching homes and
paths; use distinct home-label slugs to resolve it. The list reports home,
secondmate registry, and unreadable report-file errors; report ID commands
refuse resolution while a selected local home is incomplete.

`reports read` prints Markdown and writes the existing readable HTML format.
The HTML page is written to the Quarterdeck state directory. Use `--open` to
open the generated page in a browser. `mark-reviewed` records the current
report fingerprint; `unmark-reviewed` clears that mark. `discard` takes the
report off the list without marking it reviewed (`undiscard` reverses it);
reviewed and discarded replace each other, and a changed report returns to
needs review. `list` prints `ID`, review state (`needs review`, `reviewed`,
`discarded` or `ID collision`), title, home and relevance. `set-relevance`
marks a report `current` or `stale` (default current; stale reports leave the
review list), and `relate --to TASK` records that the report belongs with a
task, which surfaces it under "Related to this session" while that task is on
the desk or in flight. See [Keep the desk relevant](../how-to/keep-the-desk-relevant.md). See [Privacy and architecture](../explanation/privacy-and-architecture.md)
for review-state storage and [The attention model](../explanation/attention-model.md)
for how marks affect the dashboard.

| Option | Meaning |
| --- | --- |
| `--home PATH` | Firstmate home; overrides a configured `homes` list and `FM_HOME`. |
| `--output PATH` | Output HTML path; overrides `output_dir` in config. Keep it outside the repo and selected homes; those locations are refused. |
| `--config FILE` | JSON config for this command; defaults to the private user config where documented above. It may supply homes, title, and output directory. |
| `--title TEXT` | Page title; overrides `page_title` in config. |
| `--all` | Include other backlog records and all reports; [decision relevance](attention-json.md#decisions) still applies. |
| `--lavish` | Write a separate `*.lavish.html` review page with stable IDs on review cards, then open it with `lavish-axi` when available. Without Lavish, print a command hint and succeed. |
| `--no-snapshot` | Do not run a home's `bin/fm-bearings-snapshot.sh` or `bin/fm-attention.sh`; use backlog decisions with recorded board and ledger activity, and show recent work from the ledger. |

`serve` re-renders the page and its readable pages at startup and on the first
request after its 30-second cache expires. It serves them with `GET` by default
on each repeatable `--host` address (default `127.0.0.1` only) and `--port`
(default 8765; valid ports are 1 through 65535). `--allow-marks` enables the
report review request, the reaction request and the saved-view request
(`POST /api/views`, which writes only the preferences file) described in the
[serving guide](../how-to/serve-the-page.md); all other writes remain refused.
The served page reloads itself every 60 seconds, waiting while a reaction is
being written or sent, and shows its generated-at time. `service` prints a systemd user unit that runs `serve` with the given
options, or with `--write` writes it to
`${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service`; it never runs
`systemctl`. A non-loopback host exposes work data without a login. `url`
prints the address or addresses configured in the installed user
service; it does not check whether the service is running. If no unit is
installed, it labels the default `serve` address shown, which a plain
`quarterdeck serve` uses. A foreground serve started with custom `--host` or
`--port` is not detected and serves wherever those flags point. IPv6 URLs use
brackets, and scoped IPv6 zone separators use `%25` in URLs. Add `--json` for
an object containing `urls`, raw configured `hosts`, and `source`; when no unit
is installed, `source` is `default` and `note` explains the fallback. See
[Serve the always-current bearings page](../how-to/serve-the-page.md).

`board` manages one home's `<data>/captain-board.json` by task id. It uses
`--data-dir` directly when supplied, without requiring a home. Otherwise it
requires `--home` or `FM_HOME`, and uses `FM_DATA_OVERRIDE` when set, then
`<home>/data`. The data directory must already exist. It does not read the
config's home list. `upsert` adds or updates an item, `touch` records the captain's
engagement time, `remove` deletes the task's items, `active` records an operator
message (starting a new session after a gap of more than four hours, or with
`--new-session`), and `list` prints them.
Writes are atomic and locked; an unreadable or unsupported board is refused
rather than overwritten. If both `--touch` and `--touched-at` are supplied,
the explicit timestamp takes precedence. Exit status is 0 on success, 1 when
`touch` finds no item, and 2 for invalid input or an unwritable board. See
[Keep the captain board current](../how-to/keep-the-board-current.md).

`calls` sets relevance and relations for past captain calls (board items and
held backlog tasks) in Quarterdeck's local state, never in the board file. A
call is named by its task ID, or `<home-label-slug>/<task-id>` when the same ID
exists in several homes. `calls list` prints each call's ID, relevance and band.

`export` writes the cloud artifact's data snapshot: JSON to standard output, to
`--output`, and with `--artifact` an HTML page built from
`templates/artifact.html.tmpl` with the snapshot embedded. Shared-relevance
snapshots carry `decision_window_days`; their calls retain the real activity
time in `touched_at` and identify its classification with `activity_reason`.
Artifact cards and details label worker-only activity as "worked on" and do
not claim captain-message recency. Output paths must
be outside the checkout and selected homes. A failed attention view aborts
export with status 2 before writing either output. `--bodies` picks which waiting
reports carry their Markdown: `all` (default), `desk` (only reports linked to
a desk or this-week call, or related to the session) or `none`. `import` applies the artifact's
recorded mark and discard actions from a file (or `-` for standard input) to
the local state; actions for changed reports are skipped and listed. Exit
status is 0 when the file was read, even if some actions were skipped, and 2
for an unreadable file or invalid action. See
[Use the cloud artifact](../how-to/use-the-cloud-artifact.md).

`reactions import` delivers the reactions recorded in the cloud artifact
(collection `qd-inbox`; a JSON list, or `{"version": 1, "reactions": [...]}`,
of documents with `id`, `kind` of `call` or `report`, `item_id`, `reaction` one
of `approve`, `hold`, `not-now`, `question` or empty, `text` up to 1000
characters, and `at`). Oldest first, each is sent as a note through the item's
home's `bin/fm-inbox.sh note --request-id qd-<id>`; delivered ids are kept in
the local state, so none is sent twice. A reaction for an unknown item, an
invalid document, or a failed delivery is skipped and listed (a failed one
stays undelivered and is sent on the next import). `--dry-run` sends nothing.
Exit status is 0 when the file was read, and 2 for an unreadable or malformed
file. The served page's Send button posts to `POST /api/reactions` (body
`{"id", "kind", "item_id", "reaction", "text"}`) when `serve --allow-marks` is
on. The page keeps a draft's id until it is sent or edited, so an unchanged
retry reuses the same fm-inbox request id and is not delivered twice.

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
separate Firstmate home. Remote routes are listed as unavailable homes, though
the parent's snapshot may return their aggregated records for the bearings
sections. See [Configure multiple homes](../how-to/configure-multiple-homes.md)
for setup steps and [Privacy and architecture](../explanation/privacy-and-architecture.md)
for the read-only model.

Start from [`quarterdeck.example.json`](../../quarterdeck.example.json). It
contains invented example values only. Keep a real config file outside the repo
or name it `.quarterdeck.json`, which is ignored by Git.

## Data shown

Report recommendations are shown only when the report explicitly labels one.
Board recommendation chips come from the optional
[`brief.recommendation` field](captain-board.md). See [The attention
model](../explanation/attention-model.md) for dashboard inclusion and `--all`
behavior.

The page is regenerated on demand. `quarterdeck render --lavish` writes a
separate Lavish-ready file beside the normal output (for example,
`index.lavish.html`) and opens it with `lavish-axi` when that command is on
`PATH`. It prints Lavish's session URL. Without Lavish, Quarterdeck prints a
command hint and still succeeds. See [Request a Lavish page](../how-to/request-lavish-page.md)
for the workflow and [Privacy and architecture](../explanation/privacy-and-architecture.md)
for the request data and interaction boundary.
