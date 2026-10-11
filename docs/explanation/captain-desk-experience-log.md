# Captain desk experience log

A running record of how the captain board is meant to serve the person who
answers Firstmate's asks (the captain): what was tried, what worked, what did
not, and the decisions made. Update it as results come in. Add dated entries
under **Results**, move items from **Open items** when they land, and supersede
a decision with a new dated line instead of rewriting it.

This log uses generic wording only. Real task ids, project names, people and
links stay in the Firstmate home.

## Why the first board went unused

The first captain board (the grouped "waiting on you" list) was correct but
not used. The captain went back to asking in chat.

- **It listed everything that needed attention, not what the captain was
  working on.** Every open ask appeared in five fixed groups. On a measured
  home that meant about 25 board items plus more than 30 open captain holds,
  and the item the captain was actively working on that day was not on the
  board at all; it sat at the bottom under "Not yet sorted".
- **Declaring priorities was too much work.** The only way to make the page
  reflect current focus was to tell Firstmate (or someone) what to show. That
  cost more than asking in chat, so chat won.
- **The board depended on hand-edited JSON.** Firstmate had to rewrite the
  file on every change. It drifted: a day-old file, missing items, and few
  brief links (6 of 25 items).
- **Hold notes were unreadable.** Firstmate stores hold reasons in an encoded
  form (`fm-hold-v1:` followed by base64). Quarterdeck showed the encoded
  text, so items without a board entry read as noise.

## Signals evaluated

The goal was a ranking that follows the captain's attention without asking the
captain to declare it.

| Signal | Verdict |
| --- | --- |
| Captain-dated notes in backlog task bodies (`Captain YYYY-MM-DD: ...`) | Used as the main fallback. Present on every open captain hold measured; Quarterdeck already reads the backlog. Day precision only. |
| `Captain hold set: <time>` in the backlog | Used as a fallback. Says when the item started waiting, which is close to, but not the same as, attention. |
| Chat activity | Not readable by Quarterdeck (outside its boundary, and private). Only Firstmate, in the conversation, can map plain-language mentions to tasks; keyword matching was unreliable because the captain names work in plain language, not by task id. So Firstmate records it as `touched_at`. |
| Worker status timestamps | Rejected: they show work moving, not the captain's attention. |
| Firstmate verdict log, fleet ledger | Rejected: outside the boundary, or off in the measured home. |
| Report review marks | Rejected: no timestamps stored. |
| Comments on decision briefs | Strong signal; the watching worker relays it to Firstmate, which records `touched_at`. |

Comments on a published brief artifact receive Claude Code's automatic instant
reply. Every reply Firstmate writes itself starts with `FM: ` so the two can
be told apart.

## Ranking chosen

Recency bands, documented in the
[board file reference](../reference/captain-board.md#what-the-page-does-with-it):

- **On your desk**: touched within 48 hours and not deferred, open, at most
  five, then "+N more you touched recently".
- **This week**: touched within 7 days, collapsed, counts per group.
- **Parked**: older, never touched, or deferred, collapsed, counts per group,
  with a "keep, defer or drop?" prompt for items with a known touched time
  over 14 days ago.
- Within a band: merge, approve, decide, forward, read, then items not yet on
  the board; newest touched first.

`touched` is the newest of the board's `touched_at`, the latest past captain
note date, and the hold-set time. Deferral comes from a future `hold-until`
date, a blocked-by or waiting-on/for dependency on another task, or explicit
hold wording such as "deferred until ...", "hold off ... until ...", or
"waits until ...". A hold note also defers when its
"waits/waiting on|for <token>" or "after <token>" names a different task in the
same home's backlog. Trailing punctuation is stripped and task IDs are
compared exactly. Bare "parked" or "deferred" wording and self-references do
not defer an item. Unknown tokens and captain-directed asks such as "Waiting
for approval from the captain" do not establish a dependency. Future
Captain-note dates are ignored and do not defer an item.

## What was built (phases 1 and 2)

- A decision-brief template and how-to
  ([Write and publish a decision brief](../how-to/write-decision-briefs.md)).
- Optional board fields `touched_at`, `project`, and `brief {url,
  recommendation}`, still schema version 1.
- The recency-ranked board with brief links and recommendation chips, laid out
  touch-first. Backlog items missing from the board are ranked too, so a
  missing entry no longer buries active work.
- `quarterdeck board upsert|touch|remove|list`, so Firstmate keeps the board
  current with one command per change instead of hand-editing JSON
  ([Keep the captain board current](../how-to/keep-the-board-current.md)).
- Decoding of Firstmate's encoded hold notes for display and deferral.

## Results

- **First dry run (backlog fallbacks only, no `touched_at` written yet).** On
  a measured home with 32 open asks, the page led with 3 desk items, 17 in
  This week, and 12 Parked (6 of them deferred). This matched the scout's
  prediction of 3 desk items. Before hold notes were decoded, the same run put
  6 items on the desk, because deferral notes were unreadable.
- **12 of those 32 asks had no board entry** (shown as "Not yet sorted").
  That is the staleness the writer exists to fix.
- **No board item yet carries a `brief` field.** Existing brief links sit in
  `links`; Firstmate needs to move them with `quarterdeck board upsert <task>
  --brief-url ... --recommendation ...`.
- The template and board fit a 375px phone width with no horizontal scroll;
  every board tap target is at least 44px tall.

Add results here as they come in: whether the captain opens the page, how
often desk items match what they are working on, and how often Firstmate
forgets to touch.

## What works

- The hold-set and captain-note fallbacks give a usable first ranking with no
  new tracking.
- Ranking items that are missing from the board into the same bands puts
  active work on the desk even when the board is behind.
- One writer command per change is cheap enough to do every time.

## What did not work

- A complete, grouped list as the front page (see above).
- Asking the captain to say what to show.
- Hand-maintained JSON.
- Treating stored hold notes as plain text.

## Decisions

Accepted on 2026-10-11, as recommended by the scout report:

1. **Term.** "Cost of delay" for what keeps hurting while a change waits,
   paired with "Exposure" for the risk that persists.
2. **Where briefs live.** Claude artifacts linked from Quarterdeck; Lavish as
   the fallback.
3. **Recency rule.** 48-hour "On your desk" (at most 5), 7-day "This week",
   then "Parked".
4. **Remote Control.** Turn on Remote Control with push for Firstmate's
   session. This is a setting only the operator can switch on, not code.
5. **Off-network reading.** A pinned artifact mirror of the desk, not exposing
   Quarterdeck beyond its network.
6. **Voice.** Dictation, push notifications and phone read-aloud now; a local
   speech stack later.
7. **Tap-to-answer.** Pilot on one merge brief.
8. **Large touch display.** Not now.

Implementation decisions in this change:

- **The writer lives in Quarterdeck and writes only the board file.** The
  board's schema is Quarterdeck's, the file is Firstmate's, and the command
  runs only when Firstmate calls it. `render` and `serve` stay read-only.
- **`upsert` does not mark an item touched by default.** A new ask is not the
  captain's attention; until it is touched, the hold-set time ranks it.
- **Unsorted backlog items are ranked, not listed last.**

## Recommendation beyond the ask

The ranking still depends on Firstmate remembering to run `quarterdeck board
touch` whenever the captain engages. That is the same failure mode as before,
only cheaper. What the captain wants is a page that follows their attention
with no effort from anyone, so the more reliable design is to make the touch
automatic in Firstmate itself: call `quarterdeck board touch <task>` from the
Firstmate helpers that already run when the captain answers a hold, a
watched brief relays a comment, or a task is discussed by id, and call
`upsert`/`remove` from the helpers that set and clear captain holds. That is a
Firstmate change, outside this repository, so it is listed below rather than
built here.

## Open items

- **Phase 3:** a pinned "Captain's desk" artifact that Firstmate republishes
  from the board on each change, for reading off the local network
  (decision 5); a Listen button in the brief template; a tap-to-answer pilot
  on one merge brief (decision 7).
- **Remote Control with push** on Firstmate's session (decision 4): the
  operator switches it on; check whether the plan needs an owner toggle, and
  turn on trusted devices.
- **Automatic touches and board writes from Firstmate's hold helpers** (see
  the recommendation above).
- **Backfill** `brief` and `project` on existing board items.
- **Measure** desk accuracy after a week of `touched_at` writes and adjust the
  48-hour and 7-day windows if needed.
- A wall-display view was deferred with decision 8.
- The "Details by home" cards below the board show raw backlog lines and can
  overflow a phone's width when a line holds a long unbroken string, such as
  an encoded hold note. This predates the board and is not fixed here.
