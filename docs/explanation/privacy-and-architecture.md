# Privacy and architecture

Quarterdeck is a small local renderer, not a Firstmate extension. A render can
use one home from `--home` or `FM_HOME`, or several independent homes from the
`homes` array in a config file. For each home it reads the configured data
directory and adds a labeled section to one self-contained page.
`FM_DATA_OVERRIDE` selects a non-default data directory for the legacy
single-home invocation. In a home list, each entry may set its own `data_dir`;
otherwise that home's `data/` directory is used.

The inputs are `data/backlog.md`, `data/<id>/report.md` files, and the optional
`data/captain-board.json` board (see the
[board file reference](../reference/captain-board.md)). For each
configured parent home, Quarterdeck also reads the optional
`data/secondmates.md` route registry. Each local route's `home:` field points
to another Firstmate home, which gets its own labeled section underneath the
parent. That secondmate has its own `data/backlog.md` and report files. A remote
route is shown as unavailable as a separately loaded home. The parent's
bearings snapshot may still include remote secondmate rows collected through
Firstmate's routes and cache. Quarterdeck itself makes no network calls and
does not use SSH. This follows Firstmate's [secondmate route and home layout](https://github.com/kunchenguid/firstmate/blob/main/docs/configuration.md).

Quarterdeck reads these files directly. The single exception is a home's own
`bin/fm-bearings-snapshot.sh --json`, run with a short timeout so the page
agrees with Firstmate's `/bearings`; that script may refresh a parent-side
Firstmate cache as part of its own operation. `--no-snapshot` skips it, and the
page falls back to parsing the backlog when it is absent, fails, or times out.
The wrapper may also read registered remote secondmate ledgers through
Firstmate's routes and show the returned rows. Quarterdeck invokes no other
Firstmate script directly, makes no network calls itself, and writes nothing to
a home.

`quarterdeck serve` adds an HTTP listener. It answers only `GET` for generated
pages, has no write endpoints and no authentication, and binds 127.0.0.1 by
default. Binding another address exposes real work data to that network.
Each configured home is loaded independently. A missing or unreadable home
shows its own error while other sections still render.

The page may include task names, recommendations, and local paths from every
selected home. Its default location is under
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/`, outside the repo. Keep any
custom output outside the repo and selected homes, and keep real config outside
the repo as well. Quarterdeck refuses output inside its checkout or a selected
home. The page uses inline CSS and makes no network requests. `serve` is an
optional HTTP process that can run under the systemd user unit; Quarterdeck has
no write-back path into a Firstmate home.

Quarterdeck writes readable HTML copies of linked reports, reports needing
review, and the backlog beside the main page; the dependency-free Markdown
renderer escapes source HTML. `quarterdeck reports read <id>` uses the same
renderer. The [CLI reference](../reference/cli-and-config.md) documents its
output path, report IDs, and collision handling.

Reviewed marks are stored separately from rendered pages at
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/review-state/marks.json`. Each
mark stores the sha256 fingerprint of the report
bytes. Before it reads or writes this state, Quarterdeck verifies the state
directory is outside the repository checkout, all selected homes, and their
configured data directories. The state file contains report IDs and
fingerprints, not a copy of the report or Firstmate data.

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
