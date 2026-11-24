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
start the page:

```sh
quarterdeck serve
```

After installation, run `quarterdeck update` to update the checkout. The
existing `quarterdeck install` command also reruns the installer; use
`quarterdeck update --help` or `quarterdeck install --help` for command help.

See [Save items to Quarterdeck](save-items.md) and the
[CLI and configuration reference](../reference/cli-and-config.md).

## Manual installation alternative

If you prefer to manage the checkout yourself, clone the public repository and
create the command link:

```sh
git clone https://github.com/ryannmicua/quarterdeck.git ~/.local/share/quarterdeck
mkdir -p ~/.local/bin
ln -s ~/.local/share/quarterdeck/quarterdeck.py ~/.local/bin/quarterdeck
```

The config is optional. Keep any config outside the repository.
