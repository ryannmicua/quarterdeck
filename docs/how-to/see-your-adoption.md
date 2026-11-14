# See how much you use Quarterdeck

The **Adoption this week** link in the page header opens `adoption.html`, a
small phone-first page that compares this week (Monday to today, in the
machine's time zone) with last week. The goal is fewer status questions in the
Firstmate chat, because Quarterdeck already shows the answer.

| Row | Where it comes from |
| --- | --- |
| Status questions asked in chat, and how many Quarterdeck already had the answer for | `data/qd-adoption/tags.jsonl` in each home's data directory. Firstmate appends one tag per captain chat message: `{"ts", "kind": "status" \| "instruction" \| "decision" \| "discussion", "topic", "qd_had_answer": true \| false \| null}`. Quarterdeck only reads it. Optional `source: "page" | "artifact"` marks Quarterdeck-origin tags, which are excluded from chat totals. |
| What you had to ask about | The `topic` of this and last week's status tags, marked "answer was here", "had to ask" or "not checked". |
| Answers sent from Quarterdeck | Reactions recorded with source `page` or `artifact` in the review state. |
| Share of answers from Quarterdeck (approximate) | Page/artifact reactions and replies divided by those answers plus `kind: "decision"` chat tags. No answers means no observations. |
| Median first-seen wait (approximate) | Each Needs-you card's first observed appearance to the first logged page visit at or after it. Grouped by arrival week; cards without a later visit are excluded. |
| Reports arrived, reviewed, discarded | Append-only `adoption_events` in the review-state database. Arrivals are first observed by `serve --allow-marks`; triage events are recorded by CLI, page and artifact-import actions. Later report edits, discards or unmarks never change previous weeks. |
| Days visited, share of visits from a phone | `visits.jsonl`, below. |

A source that does not exist yet counts as zero and is named at the foot of the
page. Damaged lines are skipped. Only tags are kept: never the chat messages.
Triage and Needs-you history starts when collection begins, with no backfill.
The page shows "counting from <date>" for the first seven days. Appearance means
first observed by the server, and visits are deduplicated, so first-seen waits
are estimates rather than proof that a particular card was read. New card event
timestamps identify new appearances. The event log stores only kind, item ID
and observation/action time, never card text.

## The visit log

`serve --allow-marks` appends one line per load of the main page or the
adoption page to `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/visits.jsonl`
(mode 0600): `{"ts", "page": "main" | "adoption", "device": "phone" | "desktop"}`.
There is no address, user agent or content. A phone is a browser whose user agent
says Mobile, iPhone or iPod; Android requires Mobile. iPads, Android tablets
without Mobile, and unknown browsers count as desktop.
Automatic refresh requests carry a marker and are never logged, even after
midnight or a server restart. The page removes that marker from its visible URL
so later manual navigation can count again. Repeat visits to the same page from
the same kind of device within 30 minutes are deduplicated. When the file passes
128 KiB, lines older than 120 days are dropped. A read-only `serve` logs nothing.
