# CLI and configuration reference

## Commands

```text
quarterdeck add --kind resource|list|questions|note --title TEXT --description TEXT
                [--url URL --link-text TEXT] [--body-file PATH|-]
quarterdeck list [--all] [--json]
quarterdeck show ID [--json]
quarterdeck edit ID [--title TEXT] [--description TEXT] [--url URL] [--link-text TEXT] [--body-file PATH|-]
quarterdeck remove ID
quarterdeck done ID
quarterdeck reopen ID
quarterdeck render [--output PATH] [--config FILE] [--title TEXT]
quarterdeck serve [--host ADDRESS ...] [--port N] [--base-path PATH] [--tls-cert FILE --tls-key FILE] [--config FILE] [--title TEXT]
quarterdeck service [--write] [--host ADDRESS ...] [--port N] [--base-path PATH] [--tls-cert FILE --tls-key FILE] [--config FILE]
quarterdeck url [--json]
quarterdeck install [--uninstall]
quarterdeck update
quarterdeck help [COMMAND] [--json]
```

An `ID` is `Q7`, `q7` or `7`. A command that fails prints `quarterdeck: <reason>`
and exits with status 2; nothing is saved.

### Items

| Command | What it does |
| --- | --- |
| `add` | Saves a new item and prints its ID. A `resource` needs `--url` (http or https) and `--link-text`; the other kinds need `--body-file`, where `-` reads standard input. A `list` body is one entry per line (a leading `-`, `*` or `1.` is removed) or a JSON array of strings. A `questions` body is a JSON array; see [Item store](item-store.md). The body is copied in at save time. |
| `list` | Prints open items, newest first: ID, kind, saved date and title. `--all` adds done items, marked `(done)`. `--json` prints the stored items. |
| `show` | Prints one item in full, numbering list entries and questions as `Q7.1`, `Q7.2`. `--json` prints the stored item. |
| `edit` | Changes the title, description, a resource's URL or link text, or the body. The kind and ID stay. |
| `remove` | Deletes an item. Its ID is never used again. |
| `done`, `reopen` | Move an item to Done or back to Saved. |

### Pages

| Command | What it does |
| --- | --- |
| `render` | Writes `index.html` (Saved), `done.html` and one `q<N>.html` page per item. `--output PATH` names the Saved page; the others go beside it. Without it the pages go to `output_dir`, or `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/`. Pages Quarterdeck wrote earlier that are no longer needed are deleted. A page counts as Quarterdeck's only when it carries Quarterdeck's generated-page marker, so other files stay. The output must be outside the Quarterdeck checkout. |
| `serve` | Serves the same pages over HTTP, or HTTPS with `--tls-cert` and `--tls-key`. Every request reads the store, so a new item shows on the next load. `/Q7` also opens item Q7. `POST` is refused. `--host` defaults to `127.0.0.1` and may be repeated; `--port` defaults to 8765. A server bound only to loopback refuses requests whose `Host` is not loopback. |
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
