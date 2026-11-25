# Targeted testing

Run one group while changing it, then the whole suite before a pull request
(see `CONTRIBUTING.md`):

| Area | Command |
| --- | --- |
| Item commands and the store | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ItemCommandTests` |
| Classification, answer history, reports, and list filters | `PYTHONPATH=tests python3 -m unittest -v test_classification` |
| Page content | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PageTests` |
| Inline question addresses and plain-text choice values | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.QuestionAddressTests` |
| Question link validation and placement | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.QuestionLinkTests` |
| `render` | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.RenderTests` |
| Question image import, serving, and export | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.QuestionImageTests test_quarterdeck.QuestionImagePageTests` |
| `serve`, base path, host checks and the visit log | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ServeTests` |
| Card asks, replies, and acknowledgment | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ConversationTests` |
| Desk conversations, home binding, and export exclusion | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.TalkToFirstmateTests` |
| Workers snapshot and refresh | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.WorkersTests` |
| `service`, `url` and `help` | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ServiceAndHelpTests` |
| Commit guard for the item store and visit log | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PrivacyGuardTests.test_local_state_is_ignored_and_force_staging_is_rejected` |

Tests use private `XDG_STATE_HOME` and `XDG_CONFIG_HOME` directories and
invented items, and start local HTTP servers that shut themselves down.
The serve group uses headless `google-chrome` when available to verify bulk
suggestions, undo, and that conflicts and validation errors preserve drafts while the other
answers in the batch are sent. The browser checks also cover retrying a reopened
answer, exact answer times in a fixed locale and time zone, and sending suggestions
in place while preserving their text. They also cover one-line summaries,
visibility of one-word answers and Desk markers at 320, 390, and 1440 pixels,
numeric order after out-of-order delivery, and refolding a delivered correction
or expanding a pending one with the latest answer source. The library search
check covers Unicode case folding, literal terms, and exact question IDs
without revealing answer previews.
The send bar check uses 390 × 844 and 1440 × 900 viewports with one and twelve
questions. It verifies the bar at both ends of the page and after its status
message grows, including that the last answer field can scroll clear of the bar.
On the long phone page, it also checks that Undo and status messages leave the
focused control visible. A long send error scrolls inside the bar. The browser
checks also cover focus through delivery transitions. At 390, 641, and 1440
pixels, the Desk check confirms one row per fixture item and that every cell
hits its row's item link.
It also checks that the item ID labels the link when the title is invisible
and that a long project name and delivery status fit without horizontal
scrolling. A 320-pixel sheet check covers both tabs and Close library. A
zoomed viewport check verifies the sheet at 640 CSS pixels and the drawer
at a fractional width just above it.
At 140 pixels tall, browser checks cover the Send
bar with Undo and a long error, and search and results in both library layouts.
A 320 × 355 check switches tabs and reaches results past stacked library filters.
The keyboard regression simulates visual-viewport resize and pan events while
holding the layout viewport fixed. It checks Send clearance and library search
in the sheet and drawer with 60 pixels visible, then restores the full height.
The Desk history check opens a library item, updates answers, then goes Back,
Forward, and Back again. Both returns verify the same tab and search against a
fresh Desk and archive, including filters whose values disappear from the
refreshed results. After dismissal, returning from an item opened through
the Desk table and reloading the Desk both leave the library closed.

The classification tests parse served and exported HTML to verify item and
project pairs in a single Desk table, including sets with multiple prompts.
The classification browser tests use headless `google-chrome` to select
filters and check which reference rows, reviewed cards, and empty messages
are visible in the narrow and desktop layouts.

The workers browser tests use headless `google-chrome` on the Desk and the
Workers page. They check for zero Workers requests on the Desk and count
requests on entry, during a virtual minute of idle time, and after a manual
check. They cover a click during entry, reuse of fresh saved readings,
replacement of expired readings on entry, and manual recovery after a failed
saved lookup. A successful manual check replaces the
closed worker's row with the new live worker and updates the time element's
`datetime` attribute even when the source clock moves backward. The server
tests cover the elapsed-time expiry boundary, explicit checks with fresh
data in the cache, reusing a reading refreshed by another request, and
keeping the previous reading after a failed check.
The timeout test checks that the snapshot shell and its child both release a
shared pipe when the endpoint returns 502.

The conversation group uses a fake inbox to check that ask replays and
retries keep their original IDs and reject a repointed home symlink. It
checks recovery when an ask is already saved and when the first request
never arrived, and checks assets from plain and external renders. Its
headless Chrome checks cover native undo and redo of rejected asks,
option and suggestion fills, and edits with history in a sibling editor.
They check matching text, including surrounding whitespace, after native
Undo and Redo, and verify that grouped Backspaces cannot make a restored
fill askable. **Undo changes** preserves the prior draft's edit origin.
The tests also verify that an owner
can edit and save an answer after replacing a rejected ask. Tests open
**Messages** explicitly and exercise both response orders for overlapping
acknowledgments and live updates.
The whole-item checks cover the toolbar editor, the inbox target and retry ID,
separate reply acknowledgment for the item and each question, and private
reply images. The browser checks a 1200-pixel reply image in both conversations
inside an iframe with a 390 CSS-pixel viewport and checks the whole-item image
again after a thread refresh. The conversation group also checks that confirmed
conflicts return local drafts to their editors, preserve saved asks, and keep
reference items outside the conversation workflow. The closed-item check closes
the questions between saving an ask and delivering it, then verifies that the
saved ask survives the conflict and the conversation stays readable.

For a screen change, also save one item of each kind, run `quarterdeck serve`,
and check the Desk, each tab of the library pop-out (try 3, 30 and 200 saved items) and each item page at phone width (about 390 px) and
desktop width: the page scrolls to its last element and back, and nothing
scrolls sideways.
