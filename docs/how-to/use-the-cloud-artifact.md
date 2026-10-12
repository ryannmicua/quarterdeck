# Use the cloud artifact

Quarterdeck can render the same desk, this week, related section and report
list as a private Claude artifact, for use when you are in Claude rather than
on the local network. The local page and Lavish page keep working unchanged.

## Publish

1. Export the snapshot and build the page. Keep the output outside the
   Quarterdeck checkout and your Firstmate homes:

   ```sh
   quarterdeck export --artifact ~/.local/state/quarterdeck/artifact.html \
                      --output ~/.local/state/quarterdeck/snapshot.json
   ```

   `--artifact` fills [`templates/artifact.html.tmpl`](../../templates/artifact.html.tmpl)
   with the snapshot. `--output` writes the snapshot JSON alone; with neither
   flag the JSON goes to standard output.
2. Publish the page as a private Claude artifact with capabilities
   `{db: {}, downloads: true}`. Republish after each export to refresh it.

The snapshot holds what the page shows: calls (title, ask, band, project,
topic, links, relevance), the report list (title, recommendation, state,
fingerprint, relevance) and related items. It does not contain report text or
home paths, and the privacy guarantees of the
[architecture guide](../explanation/privacy-and-architecture.md) apply to the
published page: it is your work data.

## The round trip

On the artifact you can **Mark reviewed**, **Discard**, **Unmark** and
**Undiscard** reports. Each click is recorded as one document in the artifact's
own shared database (collection `qd-actions`, one document per report, latest
wins) with the report ID, the action, the report's fingerprint from the
snapshot, and a time. The page overlays those actions on the snapshot, so it
reflects your clicks immediately.

To apply them locally, save the artifact's actions as JSON and import them:

```sh
quarterdeck import quarterdeck-actions.json
```

The file can come from the artifact's **Download recorded actions as JSON**
button, or from a Firstmate session reading the `qd-actions` collection and
writing its documents as a JSON list. `quarterdeck import` accepts
`{"version": 1, "actions": [...]}` or a bare list, applies the actions oldest
first, and reports what it skipped. An action is skipped when the report
changed after the snapshot (its fingerprint no longer matches), when the ID is
unknown or ambiguous, or when a selected local home is unreadable. `--dry-run`
prints what would apply. `import` writes only Quarterdeck's local state.

Re-export after importing. The artifact ignores an action recorded before the
snapshot it is shown with, so a new snapshot never reapplies old clicks.

## What the artifact does not do

It does not set relevance or relations (those stay with Firstmate's CLI) and it
does not show report text. Read a report locally with
`quarterdeck reports read ID`.
