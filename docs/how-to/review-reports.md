# Read and track report reviews

Quarterdeck lists discovered reports from each selected Firstmate home. List
them with their review state:

```sh
quarterdeck reports list
```

When using a one-off home or a different config, pass `--home PATH` or
`--config FILE`. The command uses the same configured homes as `quarterdeck
render` by default.

Each report has a stable ID in the list. See the [CLI reference](../reference/cli-and-config.md)
for the ID format, collisions, and discovery errors.

Read a report by ID. Quarterdeck prints its Markdown and generates a readable
HTML copy; the output names the page:

```sh
quarterdeck reports read maple-harbor/amber-18
```

Add `--open` to open that HTML page in a browser.

Mark or remove a reviewed mark:

```sh
quarterdeck reports mark-reviewed maple-harbor/amber-18
quarterdeck reports unmark-reviewed maple-harbor/amber-18
```

To take a report off your list without saying you reviewed it, discard it; to
hide one-offs and decided items, see [Keep the desk relevant](keep-the-desk-relevant.md):

```sh
quarterdeck reports discard maple-harbor/amber-18
quarterdeck reports undiscard maple-harbor/amber-18
```

## Mark reports from the served page

If you use `quarterdeck serve`, start it with `--allow-marks` to get a "Mark
reviewed" and "Discard" buttons on each report waiting for review, "Unmark"
under "Reviewed reports" and "Undiscard" under "Discarded reports". The
buttons use the same code and state database as the
commands above, and the page updates after each click. See
[Mark reports reviewed from the page](serve-the-page.md#mark-reports-reviewed-from-the-page).

For dashboard placement and Lavish request behavior, see the [attention model](../explanation/attention-model.md)
and [Request a Lavish page](request-lavish-page.md).

Review-mark storage and write boundaries are described in the [privacy and architecture guide](../explanation/privacy-and-architecture.md).
