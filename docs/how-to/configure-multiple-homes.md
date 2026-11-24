# Configure multiple Firstmate homes

Register independent Firstmate homes in Quarterdeck's private user config
so one command can find your current conversation across them.

## Register and inspect homes

The default config is `${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json`. The
installer creates it if absent; `quarterdeck add` also creates it when needed.
The file is kept outside the Quarterdeck checkout with mode `0600`.

Register one or more homes:

```sh
quarterdeck add /path/to/firstmate-maple --label "Maple Harbor"
quarterdeck add /path/to/firstmate-willow
quarterdeck list
```

Each home must contain `data/backlog.md`. Without `--label`, Quarterdeck uses
the path's last component and prints the label it chose. Added paths are
expanded and normalized to absolute paths from the current working directory.
Quarterdeck refuses a duplicate normalized path or label. To unregister an
entry, pass its label or path:

```sh
quarterdeck remove "Maple Harbor"
quarterdeck remove /path/to/firstmate-willow
```

Removing an entry changes only the config; it never deletes Firstmate data.
All four commands accept `--config FILE` to use a different config file, for
example `quarterdeck add /path/to/firstmate --config ~/.config/review.json`.
`quarterdeck render` reads the default config when it exists, or an explicit
file supplied with `--config`.

## Edit the JSON directly (alternative)

The config can also be managed by hand. A home entry needs a `path`; `label`
and `data_dir` are optional. Home paths and `data_dir` values may use `~`;
relative values resolve from the config file's directory.

```json
{
  "page_title": "Quarterdeck",
  "output_dir": "~/.local/state/quarterdeck",
  "homes": [
    {"label": "Maple Harbor", "path": "~/firstmate-maple"},
    {"label": "Willow Quay", "path": "~/firstmate-willow"}
  ]
}
```

Keep the real config outside the Quarterdeck repository. It contains private
home paths and may reveal which Firstmate instances you use. Start from
[`quarterdeck.example.json`](../../quarterdeck.example.json) for invented
sample values.

## Render from the registered homes

Run:

```sh
quarterdeck render
```

The registered homes are candidates for
[the current conversation](see-only-this-session.md). Pass `--home PATH` to
limit that search to one home and its registered local secondmates.

## Include registered secondmates

Quarterdeck reads the parent's `data/secondmates.md` directly. A local route's
`home:` path supplies that secondmate's own `data/backlog.md` and reports.
Panels for in-scope secondmate work appear beneath the parent when its panel
is also visible. This is read-only discovery; Quarterdeck does
not create or edit route entries. The registry format and home layout are
described in [Firstmate configuration](https://github.com/kunchenguid/firstmate/blob/main/docs/configuration.md).

See [privacy and architecture](../explanation/privacy-and-architecture.md)
for how the parent's bearings snapshot handles remote routes.
