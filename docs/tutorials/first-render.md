# First render

This tutorial takes a user-local installation to a local review page.

## Before starting

Install Quarterdeck using the [install how-to](../how-to/install.md), and know
the path to the Firstmate home whose work should appear on the page. The
installer checks for Python 3. Quarterdeck reads that home's
`data/backlog.md` and any `data/<id>/report.md` files.

## Render and open the page

Register the home, then render the page:

```sh
quarterdeck add /path/to/firstmate
quarterdeck render
```

The add command validates the home layout and prints the label it used. The
render command prints the output path. The default is
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`, outside the repo.
Open that file in a browser, or pass it to `lavish-axi`:

```sh
lavish-axi ~/.local/state/quarterdeck/index.html
```

See [The attention model](../explanation/attention-model.md) for which reports
and backlog items appear on the page. A recommendation appears only when the
associated report includes a `Recommendation` heading or field.

## Refresh

Run `quarterdeck render` again to replace the page with a fresh snapshot.
For recurring refreshes, follow the [scheduled refresh how-to](../how-to/schedule-refresh.md).

To place several independent homes on one page, continue with
[Configure multiple homes](../how-to/configure-multiple-homes.md).
