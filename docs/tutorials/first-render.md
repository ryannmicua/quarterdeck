# First render

This tutorial takes a new checkout to a local review page.

## Before starting

Have Python 3 installed and know the path to the Firstmate home whose work
should appear on the page. Quarterdeck reads that home's `data/backlog.md` and
any `data/<id>/report.md` files.

## Render and open the page

From the Quarterdeck checkout, run:

```sh
python3 quarterdeck.py render --home /path/to/firstmate
```

The command prints the output path. The default is
`${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`, outside the repo.
Open that file in a browser, or pass it to `lavish-axi`:

```sh
lavish-axi ~/.local/state/quarterdeck/index.html
```

The page groups held items first, followed by review-ready pull requests, scout
reports, and active backlog items. A recommendation appears only when the
associated report includes a `Recommendation` heading or field.

## Refresh

Run the same `render` command again to replace the page with a fresh snapshot.
For recurring refreshes, follow the [scheduled refresh how-to](../how-to/schedule-refresh.md).

To place several independent homes on one page, continue with
[Configure multiple homes](../how-to/configure-multiple-homes.md).
