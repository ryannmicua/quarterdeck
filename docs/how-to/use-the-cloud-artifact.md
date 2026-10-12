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
   `{db: {}, downloads: true, comments: {composer_only: true}}`. Republish after
   each export to refresh it. (`composer_only` grants only the button that opens
   the comment composer; it asks for no consent and cannot write comments.)

The snapshot holds what the page shows: calls (title, ask, band, project,
topic, links, relevance), the report list (title, recommendation, state,
fingerprint, relevance) and related items. Each current, unreviewed report's
Markdown body travels in the snapshot to the private artifact; expand **Read
report** in the report list or related section to read it. Stale, discarded and
reviewed reports omit the body. The snapshot omits home metadata paths, but
report bodies may contain paths and other work data. The privacy guarantees of the
[architecture guide](../explanation/privacy-and-architecture.md) apply to the
published page: it is your work data.

## Read the header line

The line under the title says what the page could connect to. Reactions,
**Mark reviewed**, **Discard** and **Discuss with Firstmate** are always drawn;
they are disabled until the matching capability resolves. Each card then
shows a short hint (`Connecting…`, or `… unavailable, see the note at the
top.`); the header line carries the full reason.

| Header line | Meaning |
| --- | --- |
| `Connecting to the artifact database and comments…` | Claude has not answered yet. Controls are disabled and light up on their own. |
| `Connected: reactions and review actions are saved in this artifact.` | The `db` capability resolved. Reactions and report actions work. |
| `Read-only: the artifact database is not available in this view …` | `db` resolved with nothing: the page is open outside a signed-in artifact view, or was published without `db`. Reload signed in, or republish with `{db: {}}`. |
| `Discuss with Firstmate is off: …` | `comments` resolved with nothing; **Discuss with Firstmate** stays disabled. Reactions still work. |
| `Read-only: open this page as a Claude artifact …` | The page has no Claude runtime (a local file or plain browser). |
| `… failed to load (…)` | The capability module threw or rejected. |
| `… did not answer; reload the artifact.` | The capability did not answer within 15 seconds. Reload the artifact. |
| `Downloads are unavailable in this view.` | A download button was pressed without the `downloads` capability. |
| `Render error: …` | Something threw while drawing the page or one card. The rest of the page keeps working; report the message. |

Call cards with more to say have a **Details** section. Click it to read the
text; it stays open when the page refreshes. The **On your desk** badge counts
cards, not the sub-headings between them.

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

## React and discuss

Each call, related item and report card has **Approve**, **Hold**, **Not now**
and **Question** buttons, a short reply box and **Send**. Pick a reaction,
write a reply, or both. Send writes one document per reaction to the
artifact's own database (collection `qd-inbox`) with a unique `id`, the item ID,
`kind` (`call` or `report`), the reaction, the text, and a time. The task is
resolved from the item when the reaction is delivered.
The card then shows it as the latest reaction. A reaction sent from the local
page also appears as the latest reaction after the next export.

Reactions reach Firstmate when you deliver them locally:

```sh
quarterdeck reactions import quarterdeck-reactions.json
```

The file comes from the footer's **Download N recorded reactions as JSON**
button, or from a Firstmate session reading the `qd-inbox` collection and
wrapping the documents as `{"version": 1, "reactions": [...]}` (a bare list
works too). Each reaction is sent oldest first through the item's home's
`bin/fm-inbox.sh note`, which writes a durable note and wakes Firstmate.
Delivered ids are kept in the local state, so importing the same file again
sends nothing twice, and the export lists the delivered ids for the items it
shows so the download leaves them out. The export carries reactions only for
the calls, reports and related items it shows. `--dry-run` shows what would be sent. Reactions for
items that no longer exist, or whose delivery failed, are skipped and listed.

**Discuss with Firstmate** opens the claude.ai comment composer on that card.
A comment sent to Claude reaches Firstmate's session directly, with no import.
It needs the `comments` capability above; without it the button stays disabled.

## What the artifact does not do

It does not set relevance or relations (those stay with Firstmate's CLI).
To read the omitted body of a stale, discarded or reviewed report, use
`quarterdeck reports read ID` locally, or return it to current and needs review
and export a refreshed snapshot.
