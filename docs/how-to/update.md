# Update Quarterdeck

For a checkout installed at `~/.local/share/quarterdeck`, update from its
current branch with:

```sh
git -C ~/.local/share/quarterdeck pull --ff-only
```

The command preserves local-only changes by refusing a non-fast-forward
update. If the checkout has local edits, review or save those edits before
updating. The next `quarterdeck render` uses the updated code; existing HTML
remains until rendered again. Your config and its home list stay in place; no
migration is needed. A config may include a `homes` array to render several
independent homes together. See [Configure multiple homes](configure-multiple-homes.md).
