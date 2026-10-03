# Update Quarterdeck

For an installer-managed checkout, run:

```sh
quarterdeck update
```

This runs the same installer path as `quarterdeck install` and updates a clean
checkout with `git pull --ff-only`. If the checkout has tracked or untracked
local changes, it stops without updating them; review or save those changes,
then run `quarterdeck update` again. Your config and registered homes remain in
place. The next `quarterdeck render` uses the updated code; existing HTML
remains until rendered again.

`quarterdeck install` remains available to rerun the installer.

For a manually managed checkout, use the same fast-forward-only update:

```sh
git -C ~/.local/share/quarterdeck pull --ff-only
```

See [Configure multiple homes](configure-multiple-homes.md) to manage the
registered home list.
