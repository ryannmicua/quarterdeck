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

Recency bands follow the captain's engagement, while deferred work stays out
of the active desk. The [board file reference](../reference/captain-board.md#what-the-page-does-with-it)
owns the timestamp fallbacks, deferral rules, ordering, and weekly sweep.

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

## Session-scoped desk (2026-10-11)

**What went wrong with the 48-hour desk.** The desk still did not match the
conversation. Items from yesterday's session crowded it, while a question
Firstmate had asked minutes earlier in chat was missing until it was put on
the board. The captain likes that, within a session, work they do not bring up
fades from attention; `/bearings` then resurfaces everything so they can choose
what to focus on next. The page should behave the same way.

**What changed.**

- A new, smallest-possible signal: `quarterdeck board active` records each
  operator message in the board file (`operator.last_active_at`, plus the
  session's first message in `session_started_at`). It writes only the board
  file, like the other writer commands; Quarterdeck still reads no chat
  transcripts and nothing outside the home.
- With that record, the desk holds what was raised or engaged in the current
  session, grouped by when relative to the captain's messages: "Open items from
  your last message", "Still waiting on you from N minutes ago", "from an hour
  ago", "from this morning" (or afternoon, evening), "from yesterday". Items
  from earlier sessions drop to "This week" or "Parked"; nothing is lost.
- After 4 hours with no message the page turns into a bearings-style view:
  "Open items from our last session", then the rest with "This week" open.
- Boards without the record keep the 48-hour rule, so nothing changes until
  Firstmate starts calling `board active`.

**Choices and why.**

- **Session boundary: a gap of more than 4 hours between messages**, or an
  explicit `--new-session`. Quarterdeck cannot see when a chat starts, so the
  gap is the signal; 4 hours keeps a lunch break or a long meeting inside one
  session while an overnight or half-day break ends it. Firstmate passes
  `--new-session` when it knows a new chat started sooner. The same 4 hours is
  the idle threshold, so the "last session" the idle view shows is exactly the
  session the next message would close.
- **Conversation boundaries:** only explicit board touches at or after the
  session start belong to its desk. Group those touches by the exact last
  message boundary, under an hour, 1 to 2 hours, then
  calendar parts of today in local time (morning before 12:00, afternoon
  before 17:00, evening after), yesterday, and earlier. Clock words like
  "this morning" are what the captain used, so they follow the local clock
  rather than a fixed number of hours.
- **No five-item cap on a session desk.** The cap existed because 48 hours
  gathered too much; a session already bounds the desk, and capping would
  hide asks from the latest message.
- **One operator across homes.** The newest record among the shown homes'
  boards defines the session for every item.

**Still depends on Firstmate.** The desk is only as current as the
`board active`, `upsert --touch` and `touch` calls Firstmate makes; see
[Keep the captain board current](../how-to/keep-the-board-current.md#when-the-captain-sends-a-message).

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

Accepted on 2026-10-11, superseding decision 3: when operator activity is
recorded, "On your desk" is the current session with Firstmate, grouped by
message recency (see "Session-scoped desk" above); the 48-hour rule remains
the fallback for boards without that record.

Implementation decisions in this change:

- **The writer lives in Quarterdeck and writes only the board file.** The
  board's schema is Quarterdeck's, the file is Firstmate's, and the command
  runs only when Firstmate calls it. `render` and `serve` stay read-only.
- **`upsert` does not mark an item touched by default.** A new ask is not the
  captain's attention; until it is touched, backlog dates supply the ranking
  for other bands and the legacy desk. A session desk requires an explicit
  conversation touch.
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
- **Measure** desk accuracy after a week of `touched_at` and `board active`
  writes, and adjust the 4-hour session gap, the group boundaries, and the
  7-day window if needed.
- **Firstmate calls** `quarterdeck board active` on each operator message and
  `upsert --touch` for each ask it raises; that is a Firstmate change, outside
  this repository.
- A wall-display view was deferred with decision 8.
- The "Details by home" cards below the board show raw backlog lines and can
  overflow a phone's width when a line holds a long unbroken string, such as
  an encoded hold note. This predates the board and is not fixed here.
