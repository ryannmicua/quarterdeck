# Configure multiple Firstmate homes

Use a JSON config to render several independent Firstmate homes on one page.
Quarterdeck groups each home under its label and discovers local secondmates
registered by that home.

## Create a private config

Copy the example file outside the Quarterdeck checkout, then replace its
invented labels and paths with the homes you want to review:

```sh
cp quarterdeck.example.json ~/.config/quarterdeck.json
```

For example, configure two homes:

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

Each entry needs a `path`. Set `label` to a recognizable name for the page;
without it, Quarterdeck uses the path's last component. An optional `data_dir`
selects a non-default data directory for that home. Paths may use `~`; relative
home paths resolve from the config file's directory.

Keep the real config outside the Quarterdeck repository. It contains private
home paths and may reveal which Firstmate instances you use.

## Render the combined page

Run:

```sh
quarterdeck render --config ~/.config/quarterdeck.json
```

The page includes all listed homes. If one home is missing or unreadable,
Quarterdeck shows the problem in that home's section and renders the rest.
`--home PATH` overrides the list and renders that one home, including its
registered local secondmates.

## Include registered secondmates

Quarterdeck reads the parent's `data/secondmates.md` directly. It renders each
local route with its `home:` path beneath the parent, reading that home's own
`data/backlog.md` and reports. This is read-only discovery; Quarterdeck does
not create or edit registry entries. The registry's route format and home
layout are described in [Firstmate configuration](https://github.com/kunchenguid/firstmate/blob/main/docs/configuration.md).

Remote routes are included as sections that explain they cannot be read from
the local machine. Quarterdeck does not connect to remote hosts.
