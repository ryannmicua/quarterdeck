# Uninstall Quarterdeck

For the user-local installer, run its uninstall mode:

```sh
quarterdeck install --uninstall
```

It removes the command link if the installer created it and it still points
to Quarterdeck. It removes the checkout only if the installer created it and
it is still clean. The installer keeps your private config, saved items and
rendered pages, including their images. To discard them, see the
[config and render paths](../reference/cli-and-config.md), the
[item store and its images](../reference/item-store.md#store-layout), and the
[exported image paths](../reference/cli-and-config.md#rendered-images).

For a manually managed install, remove only the link and checkout you created:

```sh
rm ~/.local/bin/quarterdeck
rm -rf ~/.local/share/quarterdeck
```

Do not remove a checkout you reused for other work.
