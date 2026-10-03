# Install Quarterdeck

Quarterdeck has no package dependencies. It runs with Python 3.

## Install a user-local checkout

Clone the public repository into a user-owned tools directory:

```sh
git clone https://github.com/ryannmicua/quarterdeck.git ~/.local/share/quarterdeck
mkdir -p ~/.local/bin
ln -s ~/.local/share/quarterdeck/quarterdeck.py ~/.local/bin/quarterdeck
```

Ensure `~/.local/bin` is on `PATH`. Check the command:

```sh
quarterdeck --help
```

## Render a page

Set the Firstmate home explicitly on each run:

```sh
quarterdeck render --home /path/to/firstmate
```

The generated page defaults to `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`.
See [CLI and configuration reference](../reference/cli-and-config.md) for
custom output and config options.
