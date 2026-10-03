# Uninstall Quarterdeck

For the user-local installer, run its uninstall mode:

```sh
quarterdeck install --uninstall
```

It removes the command link and removes the checkout only when the installer
created it and it is still clean. It leaves a reused checkout or one with local
changes in place. It always keeps the private config and all Firstmate data.
To discard the config too, remove
`${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json` separately. Generated pages
are stored under `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/`; remove that
directory separately if you no longer need them.

For a manually managed install, remove only the link and checkout you created:

```sh
rm ~/.local/bin/quarterdeck
rm -rf ~/.local/share/quarterdeck
```

Do not remove a checkout you reused for other work. Uninstalling Quarterdeck
does not remove or change Firstmate data.
