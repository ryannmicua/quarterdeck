# CLI and configuration reference

## Commands

```text
quarterdeck add --kind resource|list|questions --title TEXT --description TEXT
                [--url URL --link-text TEXT] [--body-file PATH|-]
quarterdeck list
quarterdeck show ID
quarterdeck remove ID
quarterdeck render [--output PATH] [--config FILE] [--title TEXT]
quarterdeck serve [--host ADDRESS ...] [--port N] [--base-path PATH] [--tls-cert FILE --tls-key FILE] [--config FILE] [--title TEXT]
quarterdeck service [--write] [--host ADDRESS ...] [--port N] [--base-path PATH] [--tls-cert FILE --tls-key FILE] [--config FILE]
quarterdeck url [--json]
quarterdeck install [--uninstall]
quarterdeck update
quarterdeck help [COMMAND] [--json]
```

An `ID` is the displayed ID in either case, such as `Q7` or `q7`. A command that fails prints `quarterdeck: <reason>`
and exits with status 2; nothing is saved.

### Items

| Command | What it does |
| --- | --- |
| `add` | Saves a new item and prints its ID. A `resource` needs `--url` (http or https) and `--link-text`; the other kinds need `--body-file`, where `-` reads standard input. A `list` body is one entry per non-blank line, with prefixes preserved. A `questions` body is a JSON array; see [Item store](item-store.md). The body is copied in at save time. |
| `list` | Prints saved items, newest first, with ID, kind, saved date and title. |
| `show` | Prints one item in full, numbering list entries and questions as `Q7.1`, `Q7.2`. |
| `remove` | Deletes an item. Its ID is never used again. |

### Pages

| Command | What it does |
| --- | --- |
| `render` | Writes `index.html` (Saved) and one `q<N>.html` page per item. `--output PATH` names the Saved page; the others go beside it. Without it the pages go to `output_dir`, or `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/`. Item filenames `q<N>.html` are reserved. Existing destination files must carry Quarterdeck's generated-page marker. Render retires only marked item pages that are no longer needed. Separately named Saved pages are kept and share the item pages in that directory. The output must be outside the Quarterdeck checkout. |
| `serve` | Serves the same pages over HTTP, or HTTPS with `--tls-cert` and `--tls-key`. Page requests read the store, so a new item shows on the next load. Open item Q7 at `/q7.html`. `POST` is refused. `--host` defaults to `127.0.0.1` and may be repeated; `--port` defaults to 8765. A server bound only to loopback refuses requests whose `Host` is not loopback. |
| `service` | Prints a systemd user unit that runs `serve` with the same options, or writes it to `${XDG_CONFIG_HOME:-~/.config}/systemd/user/quarterdeck.service` with `--write`. |
| `url` | Prints the address the installed unit serves, or the default address when no unit is installed. |

`--base-path /qd` (or `base_path` in the config) serves every page under that
prefix for a reverse proxy. A request for the bare prefix is redirected (308)
to `/qd/`, and a path outside the prefix to the same path beneath it, such as
`/q3.html` to `/qd/q3.html`. `--base-path /` turns a configured prefix off.

`serve` still accepts `--allow-marks`, `--watch-waits`, `--no-snapshot` and
`--home`, which older service units pass, and ignores them with a notice.

### Install and help

`install` runs the user-local installer; `install --uninstall` removes what it
created. `update` updates an installed checkout. `help` prints command help and
the documentation to read; `help --json` prints a machine-readable index.

## JSON configuration

The config is optional. `render`, `serve` and `service` read `--config FILE`,
or `${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json` when it exists.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `page_title` | string | `Quarterdeck` | The heading and browser title. `--title` overrides it. |
| `output_dir` | string path | `${XDG_STATE_HOME:-~/.local/state}/quarterdeck` | Where `render` writes the pages. |
| `base_path` | string | none | The URL prefix described above. |

`homes` and `captain_waits`, which older versions read, are accepted and
ignored. Any other key is an error. See
[`quarterdeck.example.json`](../../quarterdeck.example.json).
