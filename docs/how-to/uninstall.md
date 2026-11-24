# Uninstall Quarterdeck

For the user-local installer, run its uninstall mode:

```sh
quarterdeck install --uninstall
```

It removes the command link if the installer created it and it still points
to Quarterdeck. It removes the checkout only if the installer created it and
it is still clean. The installer keeps your private config, saved items and
rendered pages. To discard them, see the [config and render paths](../reference/cli-and-config.md)
and the [item store path](../reference/item-store.md).

For a manually managed install, remove only the link and checkout you created:

```sh
rm ~/.local/bin/quarterdeck
rm -rf ~/.local/share/quarterdeck
```

Do not remove a checkout you reused for other work.
