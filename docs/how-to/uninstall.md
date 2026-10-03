# Uninstall Quarterdeck

Remove the command link and checkout used by the user-local installation:

```sh
rm ~/.local/bin/quarterdeck
rm -rf ~/.local/share/quarterdeck
```

To remove generated pages too, delete
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/`. This output directory is
separate from the Firstmate home. Uninstalling Quarterdeck does not remove or
change Firstmate data.
