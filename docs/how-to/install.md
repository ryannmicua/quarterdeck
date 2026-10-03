# Install Quarterdeck

Quarterdeck has no Python package dependencies. The installer uses Git and
requires Python 3 for running the command.

## Read and run the installer

The user-local installer clones or updates the checkout at
`~/.local/share/quarterdeck`, links `quarterdeck` into `~/.local/bin`, and
creates `~/.config/quarterdeck.json` only when it does not already exist. If
`XDG_CONFIG_HOME` is set, the config is stored there instead. It never uses
root or sudo.

To review the script before running it, download and print it first:

```sh
curl -fsSLo /tmp/quarterdeck-install.sh https://raw.githubusercontent.com/ryannmicua/quarterdeck/main/install.sh
cat /tmp/quarterdeck-install.sh
sh /tmp/quarterdeck-install.sh
```

For a one-line install, run:

```sh
curl -fsSL https://raw.githubusercontent.com/ryannmicua/quarterdeck/main/install.sh | sh
```

The installer warns if `~/.local/bin` is not on `PATH`. Once it is available,
register a home and render the dashboard:

```sh
quarterdeck add /path/to/firstmate
quarterdeck render
```

After installation, run `quarterdeck update` to update the checkout. The
existing `quarterdeck install` command also reruns the installer; use
`quarterdeck update --help` or `quarterdeck install --help` for command help.

The generated page defaults to
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`. The config and
generated page stay outside the checkout. See [CLI and configuration
reference](../reference/cli-and-config.md) for custom paths and
[Configure multiple homes](configure-multiple-homes.md) for home management.

## Manual installation alternative

If you prefer to manage the checkout yourself, clone the public repository and
create the command link:

```sh
git clone https://github.com/ryannmicua/quarterdeck.git ~/.local/share/quarterdeck
mkdir -p ~/.local/bin
ln -s ~/.local/share/quarterdeck/quarterdeck.py ~/.local/bin/quarterdeck
```

Create `~/.config/quarterdeck.json` with `{}` if you want to register homes
without specifying `--config`. Keep the config outside the repository.
