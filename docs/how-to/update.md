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

If you already have a Quarterdeck systemd unit, regenerate it once with
`quarterdeck service --write` and the same
serving options you originally used, including any `--config` flag. Run the
command from the same environment you use for `quarterdeck catch-up`, then run
`systemctl --user daemon-reload` before restarting. The regenerated unit uses
that config directory for Catch-up retention.

Restart a running `quarterdeck serve` or run `systemctl --user restart
quarterdeck.service` to use the updated code.

`quarterdeck install` remains available to rerun the installer.

For a manually managed checkout, use the same fast-forward-only update:

```sh
git -C ~/.local/share/quarterdeck pull --ff-only
```
