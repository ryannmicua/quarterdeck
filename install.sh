#!/bin/sh
set -eu

REPO_URL=${QUARTERDECK_REPO_URL:-https://github.com/ryannmicua/quarterdeck.git}
INSTALL_DIR=${HOME:?HOME must be set}/.local/share/quarterdeck
BIN_DIR=$HOME/.local/bin
COMMAND_LINK=$BIN_DIR/quarterdeck
CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
CONFIG_FILE=$CONFIG_HOME/quarterdeck.json
STATE_HOME=${XDG_STATE_HOME:-$HOME/.local/state}
STATE_DIR=$STATE_HOME/quarterdeck
STATE_FILE=$STATE_DIR/installer-state

die() {
    printf 'quarterdeck installer: %s\n' "$*" >&2
    exit 1
}

read_state_value() {
    key=$1
    if [ -f "$STATE_FILE" ]; then
        sed -n "s/^${key}=//p" "$STATE_FILE" | sed -n '1p'
    fi
}

write_state() {
    mkdir -p "$STATE_DIR"
    state_tmp=$STATE_DIR/.installer-state.$$
    (umask 077; printf 'checkout_created=%s\nlink_created=%s\n' \
        "$checkout_created" "$link_created" > "$state_tmp")
    chmod 600 "$state_tmp"
    mv -f "$state_tmp" "$STATE_FILE"
}

is_expected_link() {
    [ -L "$COMMAND_LINK" ] && [ "$(readlink "$COMMAND_LINK")" = "$INSTALL_DIR/quarterdeck.py" ]
}

uninstall() {
    checkout_created=$(read_state_value checkout_created)
    link_created=$(read_state_value link_created)
    preserve_state=no
    [ -n "$checkout_created" ] || checkout_created=no
    [ -n "$link_created" ] || link_created=no

    if [ "$link_created" = yes ] && is_expected_link; then
        rm "$COMMAND_LINK"
        printf 'Removed %s\n' "$COMMAND_LINK"
    else
        printf 'Kept %s (it was not recorded as an installer-created link).\n' "$COMMAND_LINK"
    fi

    if [ "$checkout_created" = yes ] && [ -d "$INSTALL_DIR/.git" ]; then
        checkout_root=$(git -C "$INSTALL_DIR" rev-parse --show-toplevel 2>/dev/null || true)
        if [ "$checkout_root" = "$INSTALL_DIR" ] && \
            [ -z "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=all 2>/dev/null || true)" ] && \
            [ -z "$(git -C "$INSTALL_DIR" clean -ndx 2>/dev/null || true)" ]; then
            rm -rf "$INSTALL_DIR"
            printf 'Removed installer-created checkout %s\n' "$INSTALL_DIR"
        else
            printf 'Kept checkout %s because it is not a clean installer-created checkout.\n' "$INSTALL_DIR"
            preserve_state=yes
        fi
    elif [ "$checkout_created" = yes ]; then
        printf 'Kept %s because it is no longer a Git checkout.\n' "$INSTALL_DIR"
    else
        printf 'Kept checkout %s because it was reused or was not recorded as installer-created.\n' "$INSTALL_DIR"
    fi

    if [ "$preserve_state" = no ]; then
        rm -f "$STATE_FILE"
    fi
    printf 'Kept config %s and all Firstmate data. Remove the config separately if you want to discard it.\n' "$CONFIG_FILE"
}

if [ "${1:-}" = --uninstall ]; then
    [ "$#" -eq 1 ] || die 'usage: install.sh [--uninstall]'
    uninstall
    exit 0
fi
[ "$#" -eq 0 ] || die 'usage: install.sh [--uninstall]'

command -v git >/dev/null 2>&1 || die 'Git is required to install or update Quarterdeck.'
command -v python3 >/dev/null 2>&1 || die 'Python 3 is required to run Quarterdeck.'
python3 --version >/dev/null 2>&1 || die 'Python 3 could not be started.'

checkout_created=$(read_state_value checkout_created)
link_created=$(read_state_value link_created)
[ -n "$checkout_created" ] || checkout_created=no
[ -n "$link_created" ] || link_created=no

if [ -d "$INSTALL_DIR/.git" ]; then
    checkout_root=$(git -C "$INSTALL_DIR" rev-parse --show-toplevel 2>/dev/null || true)
    [ "$checkout_root" = "$INSTALL_DIR" ] || die "$INSTALL_DIR is not a standalone Git checkout. Move it aside before installing."
    dirty=$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=all)
    [ -z "$dirty" ] || die "$INSTALL_DIR has local changes; review or save them before updating."
    git -C "$INSTALL_DIR" pull --ff-only || die 'the checkout could not be updated with a fast-forward-only pull.'
elif [ -e "$INSTALL_DIR" ] || [ -L "$INSTALL_DIR" ]; then
    die "$INSTALL_DIR exists but is not a Git checkout; move it aside before installing."
else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR" || die 'could not clone the Quarterdeck repository.'
    checkout_created=yes
    write_state
fi

mkdir -p "$BIN_DIR"
if [ -L "$COMMAND_LINK" ]; then
    is_expected_link || die "$COMMAND_LINK already points somewhere else; it was left unchanged."
elif [ -e "$COMMAND_LINK" ]; then
    die "$COMMAND_LINK already exists and is not the Quarterdeck link; it was left unchanged."
else
    ln -s "$INSTALL_DIR/quarterdeck.py" "$COMMAND_LINK"
    link_created=yes
fi
write_state

if [ -L "$CONFIG_FILE" ]; then
    die "$CONFIG_FILE is a symbolic link; it was left unchanged."
elif [ ! -e "$CONFIG_FILE" ]; then
    (umask 077; mkdir -p "$CONFIG_HOME")
    config_tmp=$CONFIG_HOME/.quarterdeck.json.$$
    (umask 077; set -C; printf '{}\n' > "$config_tmp") || die 'could not create the private config file.'
    chmod 600 "$config_tmp"
    if ln "$config_tmp" "$CONFIG_FILE"; then
        rm "$config_tmp"
    else
        rm -f "$config_tmp"
        [ -e "$CONFIG_FILE" ] || die 'could not finish creating the private config file.'
    fi
fi

printf 'Quarterdeck is installed at %s\n' "$INSTALL_DIR"
printf 'Private config: %s\n' "$CONFIG_FILE"
case ":${PATH-}:" in
    *":$BIN_DIR:"*) ;;
    *) printf 'Warning: %s is not on PATH. Add it to your shell profile.\n' "$BIN_DIR" ;;
esac
printf 'Next steps:\n  quarterdeck --help\n  quarterdeck add /path/to/firstmate\n  quarterdeck render\n'
