# See how much you use Quarterdeck

Open **Adoption** from the classic page header. It shows the status questions
asked in the current recorded conversation and how many Quarterdeck already
answered. Each topic is marked “answer was here,” “had to ask,” or “not checked.”

Firstmate appends tags to `data/qd-adoption/tags.jsonl` in the conversation’s
home. For a status question, use a line like this:

```json
{"ts":"2026-11-24T08:30:00Z","session_id":"fictional-chat","kind":"status","topic":"Harbor chart","qd_had_answer":true}
```

Use the same Claude Code conversation ID passed to
`quarterdeck board active --session-id ID`. Set `qd_had_answer` to `false` if
the page did not have the answer, or `null` if you did not check. The page
counts status tags from that home and conversation and skips damaged lines
and tags from the page or artifact. It reads tags without storing the
messages. See [See only this session](see-only-this-session.md).

Quarterdeck still records local arrival and triage events when configured to
do so. The session Adoption page displays the attributed chat tags.

## The visit log

`serve --allow-marks` appends one line per load of the main page or the
adoption page to `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/visits.jsonl`
(mode 0600): `{"ts", "page": "main" | "adoption", "device": "phone" | "desktop"}`.
There is no address, user agent or content. A phone is a browser whose user agent
says Mobile, iPhone or iPod; Android requires Mobile. iPads, Android tablets
without Mobile, and unknown browsers count as desktop.
Automatic refresh requests carry a marker and are never logged, even after
midnight or a server restart. The page removes that marker from its visible URL
so later manual navigation can count again. When the file passes 128 KiB, lines
older than 120 days are dropped. A read-only `serve` logs nothing.
