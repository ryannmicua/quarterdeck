# Watch Firstmate's activity as cards

The served page leads with a **Cards** feed, so you can see what Firstmate is
doing without opening a session. It works like the cards on Cadence's phone
page: newest first, each card shows a label, its age, a title and the first line
of its text. Tap a card to read the rest.

## What the cards are

Cards come from the same data as the "Waiting on you" section, read on each
render. The feed itself keeps no store and runs no new script; a needs-you card
for a task held for the captain also shows what the
[captain-wait watcher](track-captain-waits.md) recorded, when it runs.

| Section | Cards | Source |
| --- | --- | --- |
| Needs you | One per decision, merge or review waiting on you. Distinct gold edge and a status chip such as "Decision waiting" or "PR ready to merge". | The attention feed and the captain board. |
| Recent work, last 6 hours | One per task started. | `state/fleet-ledger.jsonl`. |
| For your information | One per result to read. | The attention feed and the captain board. |
| Done recently | Recently merged work, answered or closed captain calls, and dated finished tasks. | The fleet ledger and backlog, including registered homes. |

Sections appear in that order, and cards are newest first within each. Quiet and
stale decisions and dated decisions that are not yet due stay out. Information
cards follow the [waiting queue's priority scope](see-recent-work.md); needs-you
cards include every waiting decision and needs-you item, in priority projects or
not, and recent and done cards include work across projects. Only Recent work
and Done recently use the six-hour window; waiting items can be older.
Sections show every eligible card without a hidden count limit. Repeated runs of
one task (same home, project, event and title) fold into one card labelled with
a count, such as "Finished ×4", that lists each run newest first.

## Open the documents a card names

When a needs-you or information card's text names a document, such as
`data/<id>/report.md` or a bare `explainer.html` beside it, the card shows a
**Referenced documents** list with one link per file. A bare name resolves only
when the text includes a `data/<id>/<file>.md` or `.html` reference and all such
references name the same directory. References are collected from the title,
ask, detail, recommendation, and current and earlier briefing and clarification
text, including questions. Files resolve against the task owner's configured
data directory, including a local secondmate's; an unavailable owner or a
namespace shared by distinct home labels leaves links out.

Markdown opens rendered and HTML retains its content and scripts. A link appears
only for a readable UTF-8 regular `.md` or `.html` file of 2 MB or less that really
sits inside that data directory; missing files and links that escape it are left out.
The card text is not changed. Every opened document carries a small bar with
**Back to Quarterdeck** and **Open file by itself**. The second link opens the
original file with no bar: markdown as plain text, HTML as its own page. The bar
stays at the bottom of the viewport on HTML documents, with space reserved at
the document's end so its final content remains readable. Other site report and
backlog pages carry the back link only; standalone `reports read` output omits it.
Links also work in a rendered snapshot with a custom filename or `--lavish`; keep its
generated document pages beside the main page. For served HTML sandboxing, see
[Privacy and architecture](../explanation/privacy-and-architecture.md).

A needs-you card for a task held for the captain shows the kind of ask and a
one-line summary under its title once the captain-wait watcher has briefed it,
and opens to Firstmate's dated briefing and clarifications first, with
**Discard this wait** under `serve --allow-marks`; see
[Track what is waiting on you](track-captain-waits.md). A needs-you card opens to the ask, its options and recommendation when the text
states them, the decision brief and other links, and a link to the item on the
desk. If the desk has changed since the page loaded, that link refreshes the
page once pending replies and view edits are finished. With `serve --allow-marks`, it also carries the usual Approve, Hold, Not
now and Question buttons and reply box, which send a note through
`bin/fm-inbox.sh` exactly as on the desk. A card never acts by itself: a merge or
anything destructive still waits for your reaction, and Firstmate does the work.

## How new cards arrive

While the page is open it fetches `cards.json` from the same server every 15
seconds, and a new card slides in at its place with a short highlight. Server
refresh timing is described in the [serving guide](serve-the-page.md#run-it). A card
stays marked **New** until it has been at least half on screen for 1.2 continuous
seconds in the active tab or you open it there. Returning to the tab starts a
fresh visibility check. The **Cards** heading and an "N new" link in the page
header count the cards you have not seen. A card whose text changes
becomes New again. Restoring an expanded card after a poll does not mark its
changed content as seen. Card reactions show their absolute sent timestamp, so
the passage of time alone does not make a card New.

Which cards you have seen is kept only in your browser (`localStorage`), so each
browser and device tracks its own. If the browser blocks storage, cards simply
start New on each load. A card you are replying to is never replaced under you:
Quarterdeck defers removal, replacement and movement until the reply box is
empty and unfocused, no reaction choice is selected, and no send is pending.
Idle cards follow the latest order and timestamps on each poll.

A page saved with `quarterdeck render` is a snapshot. It shows the cards but does
not poll; reload it to see changes.

## If the feed is empty or incomplete

"Nothing new from Firstmate." means no card source has anything to show. If a
source could not be read (for example `--no-snapshot` skips the attention feed,
or a ledger is malformed) a notice above the cards says which one; run
`quarterdeck attention --check` for detail.
