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
systemctl --user show quarterdeck.service --property=FragmentPath --value
systemctl --user cat quarterdeck.service
```

From the shell you use for `quarterdeck catch-up`, run `quarterdeck service`
with the active unit's serving options, including any `--config` flag. Use the
generated `XDG_CONFIG_HOME` assignment in a drop-in for the active unit. Run
`systemctl --user edit quarterdeck.service` and add the assignment under
`[Service]` as an `Environment=` line. For example:

```ini
[Service]
Environment="XDG_CONFIG_HOME=/path/to/catch-up-config"
```

The drop-in preserves the active unit's serving arguments and works when the
manager loads the unit from a different config directory. Reload and confirm
the effective assignment before restarting:

```sh
systemctl --user daemon-reload
systemctl --user show quarterdeck.service --property=FragmentPath --property=Environment
```

Restart a running `quarterdeck serve` or run `systemctl --user restart
quarterdeck.service` to use the updated code.

`quarterdeck install` remains available to rerun the installer.

For a manually managed checkout, use the same fast-forward-only update:

```sh
git -C ~/.local/share/quarterdeck pull --ff-only
```
