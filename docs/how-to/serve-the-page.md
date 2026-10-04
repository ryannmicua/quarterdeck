# Serve the always-current bearings page

`quarterdeck serve` runs a small local web server. Each page request re-reads
your Firstmate homes, so the page shows the same four sections as Firstmate's
`/bearings` digest (Captain's Call, Recently Landed, Underway, Charted Next)
plus the reports waiting on your review. It costs no tokens and needs no
Firstmate session.

## Run it

```sh
quarterdeck serve
```

Open `http://127.0.0.1:8765/`. The page shows a "Generated at" time and reloads
itself every 60 seconds (it waits while you have a details block open). Rendered
pages are cached for 30 seconds so rapid reloads stay cheap.

| Option | Default | Meaning |
| --- | --- | --- |
| `--host ADDRESS` (alias `--bind`) | `127.0.0.1` only | Address to bind. Repeat it to listen on several addresses, all on the same port. |
| `--port N` | `8765` | Port; `0` picks a free one. |
| `--cache SECONDS` | `30` | How long a rendered page is reused. |
| `--refresh SECONDS` | `60` | How often an open page reloads itself. |
| `--home PATH`, `--config FILE` | configured homes | Same home selection as `render`. |
| `--no-snapshot` | off | Skip the Firstmate snapshot script and always parse backlogs. |

The server only answers `GET` for the main page and the generated report,
backlog and backlog-item pages. It has no write endpoints and never changes
Firstmate data.

## Where the sections come from

When a home has an executable `bin/fm-bearings-snapshot.sh`, Quarterdeck runs it
with `--json` and `FM_HOME` set, under a 15-second timeout, so the page and
`/bearings` agree. The page then fills in each Captain's Call item from the
home's backlog: the full hold reason and task body, options and recommendation
where the text gives them, when it was filed and how long it has waited, the
project, and a link to its report page. If the script is missing, fails or times
out, Quarterdeck parses the backlog itself and the page's "Sources" line says
the fallback was used and why. That script may refresh Firstmate's own cache as
part of its normal operation; pass `--no-snapshot` to avoid running it.

## Run it in the background with systemd

Print a user unit, or write it:

```sh
quarterdeck service            # print the unit
quarterdeck service --write    # write ~/.config/systemd/user/quarterdeck.service
systemctl --user daemon-reload
systemctl --user enable --now quarterdeck.service
```

`service` accepts the same `--host`/`--bind` (repeatable, default 127.0.0.1 only), `--port`, `--home` and `--config` options as `serve`, and it never runs
`systemctl` itself. The unit it writes looks like:

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
