# Update Quarterdeck

For the installer-managed checkout, rerun the installer:

```sh
quarterdeck install
```

It updates a clean checkout with `git pull --ff-only`. If the checkout has
tracked or untracked local changes, the installer stops without updating it;
review or save those changes, then run the installer again. Your config and
registered homes remain in place. The next `quarterdeck render` uses the
updated code; existing HTML remains until rendered again.

For a manually managed checkout, use the same fast-forward-only update:

```sh
git -C ~/.local/share/quarterdeck pull --ff-only
```

See [Configure multiple homes](configure-multiple-homes.md) to manage the
registered home list.
