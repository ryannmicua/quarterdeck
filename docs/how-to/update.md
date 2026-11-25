# Update Quarterdeck

For an installer-managed checkout, run:

```sh
quarterdeck update
```

This runs the same installer path as `quarterdeck install` and updates a clean
checkout with `git pull --ff-only`. If the checkout has tracked or untracked
local changes, it stops without updating them; review or save those changes,
then run `quarterdeck update` again. Your config and saved items remain in
place.

If you already have a Quarterdeck systemd unit, check the file the user
manager loads and its existing overrides:

```sh
systemctl --user show quarterdeck.service --property=FragmentPath --property=ExecStart --property=MainPID
systemctl --user cat quarterdeck.service
```

Preserve the active service's config before changing its environment. If its
effective `ExecStart` already has `--config`, keep that file. Otherwise, identify
the default `quarterdeck.json` the service used from the running process's
`XDG_CONFIG_HOME` or, if unset or empty, its `HOME/.config`. Use the service's
environment for this step. For a running service, replace `PID` with the
`MainPID` shown above to inspect those two variables:

```sh
tr '\000' '\n' </proc/PID/environ | sed -n '/^XDG_CONFIG_HOME=/p; /^HOME=/p'
```

Use the absolute path to that serving config, resolving any symlinks. If the
service was using built-in defaults because it had no config file, create a
separate serving config containing `{}` and use its absolute path. If you
cannot identify the settings the service was using, recover them before
changing its environment.

From the shell you use for `quarterdeck catch-up`, run `quarterdeck service`
with the active unit's serving options and `--config` pointing to the serving
file identified above. Use the generated `XDG_CONFIG_HOME` assignment in a
drop-in for the active unit. Run `systemctl --user edit quarterdeck.service`
and add the assignment under `[Service]` as an `Environment=` line. If the
active command lacked `--config`, also add an empty `ExecStart=` followed by
the full active command with `--config` and the serving path appended. For
example, a unit that previously ran `quarterdeck serve` with the default port
might use:

```ini
[Service]
Environment="XDG_CONFIG_HOME=/path/to/catch-up-config"
ExecStart=
ExecStart=/path/to/quarterdeck serve --config /path/to/serving-config.json
```

Keep the active command's executable and other serving arguments when
replacing `ExecStart`. Reload and confirm the effective command, serving file,
and config directory before restarting:

```sh
systemctl --user daemon-reload
systemctl --user show quarterdeck.service --property=FragmentPath --property=ExecStart --property=Environment
```

Restart a running `quarterdeck serve` or run `systemctl --user restart
quarterdeck.service` to use the updated code.

`quarterdeck install` remains available to rerun the installer.

For a manually managed checkout, use the same fast-forward-only update:

```sh
git -C ~/.local/share/quarterdeck pull --ff-only
```
