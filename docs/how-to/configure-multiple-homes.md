# Configure multiple Firstmate homes

Register independent Firstmate homes in Quarterdeck's private user config and
render them together. Each home appears under its label, with local secondmates
registered by that home beneath it.

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

## Render the combined page

Run:

```sh
quarterdeck render
```

The page includes all registered homes. If a home is missing or unreadable,
Quarterdeck shows the problem in that home's section and renders the rest.
`quarterdeck render --home PATH` overrides the list and renders that one home,
including its registered local secondmates.

## Include registered secondmates

Quarterdeck reads the parent's `data/secondmates.md` directly. It renders each
local route with its `home:` path beneath the parent, reading that home's own
`data/backlog.md` and reports. This is read-only discovery; Quarterdeck does
not create or edit route entries. The registry format and home layout are
described in [Firstmate configuration](https://github.com/kunchenguid/firstmate/blob/main/docs/configuration.md).

Remote routes are included as sections that explain they cannot be read from
the local machine. Quarterdeck does not connect to remote hosts.
