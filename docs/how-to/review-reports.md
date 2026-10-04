# Read and track report reviews

Quarterdeck finds `data/<task-id>/report.md` files in each selected Firstmate
home. List them with their review state:

```sh
quarterdeck reports list
```

When using a one-off home or a different config, pass `--home PATH` or
`--config FILE`. The command uses the same configured homes as `quarterdeck
render` by default.

Each report ID has the form `<home-label-slug>/<task-id>`. The label slug is
the home label case-folded, with each run of characters other than `a` through
`z` or `0` through `9` replaced by `-`, and leading or trailing `-` removed.
If it becomes empty, it is `home`. The task ID is the report's immediate
parent directory name, unchanged. For example, home label `Maple Harbor` and
task ID `amber-18` produce
`maple-harbor/amber-18`. Keep the label stable to keep the ID stable.

If two discovered reports produce the same ID, `reports list` labels both
`ID collision`. `reports read`, `mark-reviewed`, and `unmark-reviewed` refuse
that ID and list the matching homes and paths. Give the homes distinct labels
whose slugs differ, then retry. The command never picks one report arbitrarily.
The list also reports home, secondmate registry, and unreadable report-file
errors. Report ID commands refuse resolution while a selected local home is
incomplete, since it could contain another report with the same ID.

Read a report by ID. Quarterdeck prints its Markdown and writes a readable HTML
copy in its state directory; the output names the generated page:

```sh
quarterdeck reports read maple-harbor/amber-18
```

Add `--open` to open that HTML page in a browser.

Mark or remove a reviewed mark:

```sh
quarterdeck reports mark-reviewed maple-harbor/amber-18
quarterdeck reports unmark-reviewed maple-harbor/amber-18
```

The default dashboard's **Reports needing review** section links every
discovered report that has no mark for its current contents, including reports
not linked from an open backlog card. With `--all`, each report appears once in
**All scout reports** with its review state. `quarterdeck render --lavish`
adds the existing request control to each report needing review. It queues a
prompt for a listener; Quarterdeck does not send it, listen, poll, or generate
the requested page. See [Request a Lavish page](request-lavish-page.md).

Review marks are stored at
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/review-state/marks.json`. Each
mark records a sha256 fingerprint of the report bytes. Editing a report makes
it need review again. The state file is outside selected Firstmate homes, their
configured data directories, and the Quarterdeck checkout; Quarterdeck refuses
to use a state directory inside any of those locations. No Firstmate file is
changed.
