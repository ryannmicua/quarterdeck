# See how much you use Quarterdeck

The **Adoption this week** link in the page header opens `adoption.html`, a
small phone-first page that compares this week (Monday to today, in the
machine's time zone) with last week. The goal is fewer status questions in the
Firstmate chat, because Quarterdeck already shows the answer.

| Row | Where it comes from |
| --- | --- |
| Status questions asked in chat, and how many Quarterdeck already had the answer for | `data/qd-adoption/tags.jsonl` in each home's data directory. Firstmate appends one tag per captain chat message: `{"ts", "kind": "status" \| "instruction" \| "decision" \| "discussion", "topic", "qd_had_answer": true \| false \| null}`. Quarterdeck only reads it and counts `status` tags. |
| What you had to ask about | The `topic` of this and last week's status tags, marked "answer was here", "had to ask" or "not checked". |
| Answers sent from Quarterdeck | Reactions recorded with source `page` or `artifact` in the review state. |
| Reports arrived, reviewed, discarded | A report's file time, and the time of its reviewed or discarded mark in the review state. |
| Days visited, share of visits from a phone | `visits.jsonl`, below. |

A source that does not exist yet counts as zero and is named at the foot of the
page. Damaged lines are skipped. Only tags are kept: never the chat messages.

## The visit log

`serve --allow-marks` appends one line per load of the main page or the
adoption page to `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/visits.jsonl`
(mode 0600): `{"ts", "page": "main" | "adoption", "device": "phone" | "desktop"}`.
There is no address, user agent or content. A phone is a browser whose user agent
says Mobile, Android or iPhone; tablets and everything else count as desktop.
The page reloads itself every minute, so a repeat load of the same page from the
same kind of device within 30 minutes is not logged again. When the file passes
128 KiB, lines older than 120 days are dropped. A read-only `serve` logs nothing.
