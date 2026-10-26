# Watch Firstmate's activity as cards

The served page leads with a **Cards** feed, so you can see what Firstmate is
doing without opening a session. It works like the cards on Cadence's phone
page: newest first, each card shows a label, its age, a title and the first line
of its text. Tap a card to read the rest.

## What the cards are

Cards come from the same data as the "Waiting on you" section, read on each
render. Quarterdeck keeps no card store and runs no new script.

| Section | Cards | Source |
| --- | --- | --- |
| Needs you | One per decision, merge or review waiting on you. Distinct gold edge and a status chip such as "Decision waiting" or "PR ready to merge". | The attention feed and the captain board. |
| Recent work, last 6 hours | One per task merged or started. | `state/fleet-ledger.jsonl`. |
| For your information | One per result to read. | The attention feed and the captain board. |

Sections appear in that order, and cards are newest first within each. Quiet and
stale decisions, dated decisions that are not yet due, and items outside your
priority projects stay out, the same as on "Waiting on you". Work older than six
hours is not shown as cards.

A needs-you card opens to the ask, its options and recommendation when the text
states them, the decision brief and other links, and a link to the item on the
desk. With `serve --allow-marks`, it also carries the usual Approve, Hold, Not
now and Question buttons and reply box, which send a note through
`bin/fm-inbox.sh` exactly as on the desk. A card never acts by itself: a merge or
anything destructive still waits for your reaction, and Firstmate does the work.

## How new cards arrive

While the page is open it fetches `cards.json` from the same server every 15
seconds (the server re-reads its homes at most every 30 seconds), and a new card slides in at its place with a short highlight. A card
stays marked **New** until it has been at least half on screen for 1.2 seconds or
you open it. The **Cards** heading, a "N new" link in the page header, and the
browser tab title count the cards you have not seen. A card whose text changes
becomes New again.

Which cards you have seen is kept only in your browser (`localStorage`), so each
browser and device tracks its own. If the browser blocks storage, cards simply
start New on each load. A card you are replying to is never replaced under you:
Quarterdeck waits until the reply box is empty and unfocused.

A page saved with `quarterdeck render` is a snapshot. It shows the cards but does
not poll; reload it to see changes.

## If the feed is empty or incomplete

"Nothing new from Firstmate." means no card source has anything to show. If a
source could not be read (for example `--no-snapshot` skips the attention feed,
or a ledger is malformed) a notice above the cards says which one; run
`quarterdeck attention --check` for detail.
