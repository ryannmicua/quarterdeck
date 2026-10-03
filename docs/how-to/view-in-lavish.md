# View a review page in Lavish

Use Lavish when you want to annotate Quarterdeck's snapshot and queue feedback
for a collaborator.

Render a separate Lavish-ready page:

```sh
quarterdeck render --lavish
```

Quarterdeck writes `index.lavish.html` beside the normal `index.html` output.
If `lavish-axi` is on `PATH`, Quarterdeck opens the Lavish session and prints
the session URL. Otherwise, it prints the command to open the page later:

```sh
lavish-axi ~/.local/state/quarterdeck/index.lavish.html
```

Each review card has a stable ID so annotations remain attached to a specific
item. Use Lavish's annotation and queued feedback controls in its review page.
Quarterdeck does not poll for feedback or start a listener. The ordinary
`index.html` remains available as a self-contained page that can be opened
directly in a browser.
