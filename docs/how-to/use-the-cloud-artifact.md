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
fingerprint, relevance) and related items. Each current, unreviewed report's
Markdown body travels in the snapshot to the private artifact; expand **Read
report** in the report list or related section to read it. Stale, discarded and
reviewed reports omit the body. The snapshot omits home metadata paths, but
report bodies may contain paths and other work data. The privacy guarantees of the
[architecture guide](../explanation/privacy-and-architecture.md) apply to the
published page: it is your work data.

## The round trip

On the artifact you can **Mark reviewed**, **Discard**, **Unmark** and
**Undiscard** reports. Each click is recorded as one document in the artifact's
own shared database (collection `qd-actions`, one document per report, latest
wins) with the report ID, the action, the resulting state, the report's fingerprint from the
snapshot, and a time. The page overlays those actions on the snapshot, so it
reflects your clicks immediately.

To apply them locally, save the artifact's actions as JSON and import them:

```sh
quarterdeck import quarterdeck-actions.json
```

The file can come from the artifact's **Download recorded actions as JSON**
button, or from a Firstmate session reading the `qd-actions` collection and
writing only documents newer than the current snapshot's `generated_at` whose
fingerprints match the listed reports. Wrap those documents as
`{"version": 1, "generated_at": "<snapshot time>", "actions": [...]}`.
`quarterdeck import` accepts this envelope or a bare list, applies the actions oldest
first, and reports what it skipped. An action is skipped when the report
changed after the snapshot (its fingerprint no longer matches), when the ID is
unknown or ambiguous, or when a selected local home is unreadable. `--dry-run`
prints what would apply. `import` writes only Quarterdeck's local state.

Re-export after importing. The artifact ignores and excludes from downloads
actions recorded at or before its snapshot time. Import also skips those actions
when the envelope includes `generated_at`. A bare list has no snapshot cutoff;
filter database documents before saving such a list to avoid replaying old clicks.

## What the artifact does not do

It does not set relevance or relations (those stay with Firstmate's CLI).
To read the omitted body of a stale, discarded or reviewed report, use
`quarterdeck reports read ID` locally, or return it to current and needs review
and export a refreshed snapshot.
