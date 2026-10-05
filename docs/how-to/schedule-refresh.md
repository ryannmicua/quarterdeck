# Refresh on a schedule

For a page that is always current without scheduling, see
[Serve the always-current bearings page](serve-the-page.md).

Quarterdeck does not schedule rendered snapshots itself. A user's cron job or
systemd timer can run the normal `render` command when a recurring snapshot is
useful; use `serve` for the always-current page.
For a combined page, pass `--config` with the private config that lists the
homes instead of using `--home`.

## Cron example

Replace `/path/to/firstmate` with the Firstmate home for this page. This example
refreshes once each hour and keeps output in the default state directory:

```cron
0 * * * * /usr/local/bin/quarterdeck render --home /path/to/firstmate
```

If the command is installed under `~/.local/bin`, use that full path in cron.
Cron has a limited environment; set `XDG_STATE_HOME` on the command when a
non-default state directory is needed. Do not point output into the Quarterdeck
repository.

To refresh the combined view, use the config file:

```cron
0 * * * * /usr/local/bin/quarterdeck render --config ~/.config/quarterdeck.json
```

## systemd timer example

Create a user service at `~/.config/systemd/user/quarterdeck-render.service`:

```ini
[Unit]
Description=Render the local Quarterdeck review page

[Service]
Type=oneshot
ExecStart=%h/.local/bin/quarterdeck render --home /path/to/firstmate
```

Then create `~/.config/systemd/user/quarterdeck-render.timer`:

```ini
[Unit]
Description=Refresh the local Quarterdeck review page hourly

[Timer]
OnBootSec=5m
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
```

Enable it for the user:

```sh
systemctl --user daemon-reload
systemctl --user enable --now quarterdeck-render.timer
```
