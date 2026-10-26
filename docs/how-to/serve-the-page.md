# Serve the always-current review page

`quarterdeck serve` runs a small local web server. It reads your Firstmate homes
at startup and again on the first page request after its 30-second cache expires.
When any selected home or registered secondmate has a captain board, its
list leads the page; otherwise the shared waiting view leads, followed by
Firstmate's `/bearings` sections (Captain's Call, Underway, Charted Next).
See [See recent work](see-recent-work.md) for the waiting view and recent-work display.
Reports waiting on your review also appear. See the
[captain board reference](../reference/captain-board.md) for its file and display
rules. Serving costs no tokens and needs no Firstmate session.

## Run it

```sh
quarterdeck serve
```

Open `http://127.0.0.1:8765/`. The page shows a "Generated at" time and reloads
itself every 60 seconds, restoring open items and sections after each refresh
when browser session storage is available. Rendered pages are cached for 30
seconds so rapid reloads stay cheap. Automatic refresh waits while a reaction
is being written or sent, or the view editor has unsaved changes or a pending
save.

To see the serving address, run `quarterdeck url`. See the
[CLI reference](../reference/cli-and-config.md) for how it determines the
address and for its JSON output.

| Option | Default | Meaning |
| --- | --- | --- |
| `--host ADDRESS` | `127.0.0.1` only | Address to bind. Repeat it to listen on several addresses, all on the same port. |
| `--port N` | `8765` | Port from 1 through 65535. |
| `--home PATH` | unset | Select one home, overriding configured homes and `FM_HOME`. |
| `--config FILE` | private user config, if present | Select homes with the same rules as `render`. |
| `--title TEXT` | configured title | Override the page title. |
| `--no-snapshot` | off | Skip the Firstmate snapshot script and always parse backlogs. |
| `--allow-marks` | off | Enable the "Mark reviewed", "Unmark", "Discard" and "Undiscard" buttons for reports, reactions sent to Firstmate, and saving briefing views from the page. |

By default the server only answers `GET` for the main page and the generated
report, backlog and backlog-item pages. It has no write endpoints. With
`--allow-marks` it also accepts a `POST` that marks or
unmarks a known report, a `POST` that sends a reaction (see below) and a `POST`
that saves a briefing view to the preferences file;
everything else is still refused. When
snapshots are enabled, Firstmate's wrapper may refresh its cache and read
registered remote secondmate ledgers through its own routes; `--no-snapshot`
skips that behavior.

## Mark reports reviewed from the page

```sh
quarterdeck serve --allow-marks
```

The page then shows "Mark reviewed" on each report waiting for review, and a
collapsed "Reviewed reports" section with "Unmark". Each click calls the same
code as `quarterdeck reports mark-reviewed` / `unmark-reviewed`, stores the
same content fingerprint in the same state database, and reloads the page. A
report that changes afterwards returns to "needs review". **Discard** takes a
report off the list without marking it reviewed; discarded reports are in a
collapsed "Discarded reports" section with **Undiscard**.

There is no key or login: anyone who can open the page can mark or unmark
reports, and clicking the button just saves the mark. See
[Privacy and architecture](../explanation/privacy-and-architecture.md) for
request restrictions and review-mark storage. `quarterdeck service
--allow-marks` writes the flag into the systemd unit. Backlog and board items
cannot be marked.

When you bind to a non-loopback address with `--allow-marks`, everyone who can
reach the page can change review marks, send reactions and edit saved views.
Use marks only on a network you trust, or put the server behind an
authenticating TLS reverse proxy.

## React to calls and reports from the page

With `--allow-marks`, each desk call, related item and report also has
**Approve**, **Hold**, **Not now** and **Question** buttons, a short reply box
and **Send**. Pick a reaction, write a reply, or both, then Send. Quarterdeck
hands one line (item ID, task ID, reaction, your text and the time) to the
item's home through its `bin/fm-inbox.sh note`, so Firstmate receives it as a
note and wakes, and it routes the answer to you or the workers. The card shows
"Sent to Firstmate" and then the latest reaction from either this page or the
cloud artifact. If the home has no executable `bin/fm-inbox.sh`, or it fails,
the card says "Not sent" and nothing is recorded; send it again.

As with marks, there is no key: anyone who can open the page can send
reactions as you, so use `--allow-marks` only on a network you trust.

## Where the sections come from

When a home has an executable `bin/fm-bearings-snapshot.sh`, Quarterdeck runs it
with `--json` and `FM_HOME` set, under a 15-second timeout, so the page and
`/bearings` agree. For decisions with a matching local backlog item, the page
adds hold context, task body, options and recommendation where present, filing
date, project, and a report link. Remote decisions retain the snapshot summary
and point to the secondmate home for full background. If the script is
missing, fails or times out, Quarterdeck parses the backlog itself and the
page's "Sources" line says the fallback was used and why. That script may
refresh Firstmate's own cache as part of its normal operation; pass
`--no-snapshot` to avoid running it.

## Run it in the background with systemd

Print a user unit, or write it:

```sh
quarterdeck service            # print the unit
quarterdeck service --write    # write ${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service
systemctl --user daemon-reload
systemctl --user enable --now quarterdeck.service
```

`service` accepts the same `--host` (repeatable, default 127.0.0.1 only),
`--port`, `--home` and `--config` options as `serve`, plus `--allow-marks` to
enable report review marks in the unit. It never runs `systemctl` itself. The
unit it writes looks like:

```ini
[Unit]
Description=Quarterdeck read-only Firstmate bearings page
After=network.target

[Service]
ExecStart="/usr/bin/python3" "/path/to/quarterdeck.py" "serve" "--host" "127.0.0.1" "--port" "8765"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

To keep it running when you are logged out, run `loginctl enable-linger $USER`.

For example, `quarterdeck service --host 127.0.0.1 --host ::1 --write` binds
both loopback families; the unit carries one `--host` per address.

## Security: loopback by default

The page contains real task names, hold reasons, reports and paths. There is
**no login**. Binding a non-loopback address (for example `--host 0.0.0.0`)
exposes that work data to everyone who can reach the port on that network.
Quarterdeck prints a warning when you do. Prefer the default, and reach it
remotely through an SSH tunnel or an authenticated reverse proxy you control.
