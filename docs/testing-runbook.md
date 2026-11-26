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
| Back to the list and Next unread, and the Desk tab and Hold filters they return to | `PYTHONPATH=tests python3 -m unittest -v test_list_context` |
| Card asks, replies, and acknowledgment | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ConversationTests` |
| Paragraphs, lists and bold in message text | `PYTHONPATH=tests python3 -m unittest -v test_message_format` |
| Desk conversations, home binding, and export exclusion | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.TalkToFirstmateTests` |
| Workers snapshot and refresh | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.WorkersTests` |
| Catch-up store, CLI and owner acknowledgment endpoint | `PYTHONPATH=tests python3 -m unittest -v test_catch_up` |
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
scrolling. A 320-pixel sheet check covers both tabs and Close Hold. A
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
the Desk table and reloading the Desk both leave Hold closed.

The classification tests parse served and exported HTML to verify item and
project pairs in a single Desk table, including sets with multiple prompts.
The classification browser tests use headless `google-chrome` to select
filters and check which reference rows, reviewed cards, and empty messages
are visible in the narrow and desktop layouts.

The Desk replay browser check delays a start until a stale notice has shown
reload guidance. It verifies that the start result and the stale notice
guidance, which arrive while **For review** is open, show in the shared result
above the open tab, that the notice keeps its guidance on **Conversations**,
and that replay keeps one Hold result per topic. The first-start check
searches Hold by both title and item ID without reloading. The conversation
page checks click **Pin**, **Resolve**, **Archive** and **Move** on the page, and
verify that the labels and marks follow the saved state, that an archived
conversation hides **Pin**, and that a refusal shows beside the controls.
The replay check
verifies that the initial and inserted rows show October 2 in Tokyo for a
message saved at 18:30 UTC on October 1. The Desk tab check uses the old
bookmark fragment to verify that Conversations still opens first and that
clicking or using the keyboard leaves the address unchanged.

The mailbox browser checks in `tests/test_mailbox.py` run against a live test
server. At 1440 pixels wide they select a row and wait for the item to open in
the pane, without its site header or Back link. They check that the reply is
marked read, that the row keeps its place under **Unread** until the next row
opens, and that **Next unread** visits each reply before moving to the next
conversation. They change the scope while reading and check both navigation
links. **Deal with later** moves a question set under **Later** and leaves it
open. An archive in the pane updates Hold while a pending notice keeps its
disabled control and refusal feedback. The checks cover **Unread only**, the
destination of a saved link opened from **References**, and the pane beside
**Under way**. At 390 pixels wide they open the dedicated compose view and
return to the list, then reopen the unsent draft. A fixture check opens
folded messages in a conversation thread.

The grouping browser checks fill the **Project** filter with invented
projects and topics and verify which rows each choice shows, including
**Ungrouped** and an empty project. They create, rename and cancel a project
and a topic. On a conversation's page they move it into a topic and back to
**Ungrouped**, and check that a refused move keeps the form open and says so. A scoped start
must carry its project and topic. Phone-width checks verify that the
filter, the name form and the move form never scroll the page sideways and that
every control is at least 44 pixels tall.

The Catch-up browser check opens the Desk with saved notices and an
open question. At phone width it checks that the notices sit under **Unread**
on **Conversations** and not in **For review**, and checks their details and links. It
acknowledges a notice and then acknowledges the remaining notices, checks
the tab count, and verifies that the question stays in **For review**. The browser
checks also cover stale and incomplete acknowledgments, a deleted item link,
and restoring empty guidance after reading the final notice.

The workers browser tests use headless `google-chrome` on the Desk, Watch, and
Workers. The Watch check opens the page before and after a manual Workers
check, verifies the saved roster and time, and counts fleet script calls.
The Workers tests check for zero Workers requests on the Desk and count
requests on entry, during a virtual minute of idle time, and after a manual
check. They cover a click during entry, reuse of fresh saved readings,
a saved reading shown on entry without a check even after it expires, two overlapping views that receive
the same roster and timestamp from one fleet check, and manual recovery after
a failed saved lookup. A successful
manual check replaces the closed worker's row with the new live worker and
updates the time element's `datetime` attribute even when the source clock
moves backward. The server tests cover the elapsed-time expiry boundary,
expiry after simulated suspend, explicit checks with fresh data in the cache,
reusing a reading refreshed by another request, and keeping the previous
reading after a failed check.
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
The set discussion checks cover mixed and message-only sends, the inbox target
and retry ID, separate reply acknowledgment for the item and each question,
and private reply images. The browser checks a 1200-pixel reply image in both
threads inside an iframe with a 390 CSS-pixel viewport and checks the set image
again after a thread refresh. The standalone conversation image regression
(`test_conversation_message_images_fit_inside_the_message`) runs at phone
width. The conversation group also checks that confirmed
conflicts return local drafts to their editors, preserve saved asks, and keep
reference items outside the conversation workflow. The closed-item check closes
the questions between saving an ask and delivering it, then verifies that the
saved ask survives the conflict and the discussion stays readable. The closed
discussion refresh check covers the same-origin framing rule and a reply arriving in a
read-only tab after a question reopens.

For a screen change, also save one item of each kind, run `quarterdeck serve`,
and check the Desk, each tab of Hold pop-out (try 3, 30 and 200 saved items) and each item page at phone width (about 390 px) and
desktop width: the page scrolls to its last element and back, and nothing
scrolls sideways.
