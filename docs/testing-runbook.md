# Targeted testing

Run one group while changing it, then the whole suite before a pull request
(see `CONTRIBUTING.md`):

| Area | Command |
| --- | --- |
| Item commands and the store | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ItemCommandTests` |
| Classification, answer history, reports, and list filters | `PYTHONPATH=tests python3 -m unittest -v test_classification` |
| Page content | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PageTests` |
| Inline question addresses and plain-text choice values | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.QuestionAddressTests` |
| Question link validation and Desk card previews | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.QuestionLinkTests` |
| `render` | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.RenderTests` |
| Question image import, serving, and export | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.QuestionImageTests test_quarterdeck.QuestionImagePageTests` |
| `serve`, base path and host checks | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ServeTests` |
| Card asks, replies, and acknowledgment | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ConversationTests` |
| Workers snapshot and refresh | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.WorkersTests` |
| `service`, `url` and `help` | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ServiceAndHelpTests` |
| Saved item commit guard | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PrivacyGuardTests.test_item_store_is_ignored_and_force_staging_is_rejected` |

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
checks also cover focus through delivery transitions and the hit targets for
the Desk card action and its independent links. A 320-pixel sheet check covers
both tabs and Close library. A zoomed viewport check verifies the sheet at
640 CSS pixels and the drawer at a fractional width just above it.
At 140 pixels tall, browser checks cover the Send
bar with Undo and a long error, and search and results in both library layouts.
A 320 × 355 check switches tabs and reaches results past stacked library filters.
The keyboard regression simulates visual-viewport resize and pan events while
holding the layout viewport fixed. It checks Send clearance and library search
in the sheet and drawer with 60 pixels visible, then restores the full height.
The Desk history check opens a library item, updates answers, then goes Back,
Forward, and Back again. Both returns verify the same tab and search against a
fresh Desk and archive, including filters whose values disappear from the
refreshed results. After dismissal, returning from a Desk card and
reloading the Desk both leave the library closed.

The classification browser test uses headless `google-chrome` to select
filters and check which reference rows, reviewed cards, and empty messages
are visible in the narrow and desktop layouts.

The workers browser test uses headless `google-chrome` to count actual snapshot
requests on entry, during a virtual minute of idle time, and after a refresh click.
It also verifies that the click replaces the closed worker's row with the new live
worker and updates the refresh time element's `datetime` attribute after the
source clock moves backward.
The timeout test checks that the snapshot shell and its child both release a
shared pipe when the endpoint returns 502.

The conversation group uses a fake inbox to check that ask replays and
retries keep their original IDs and reject a repointed home symlink. It
checks recovery when an ask is already saved and when the first request
never arrived, and checks assets from plain and external renders. Its
headless Chrome checks cover native undo and redo of rejected asks,
option and suggestion fills, and edits with history in a sibling editor.
They check matching text after native Undo, Redo, and **Undo changes**, and
verify that grouped Backspaces cannot make a restored fill askable.
The tests also verify that an owner
can edit and save an answer after replacing a rejected ask. Tests open
**Messages** explicitly and exercise both response orders for overlapping
acknowledgments and live updates.
The whole-item checks cover the toolbar editor, the inbox target and retry ID,
separate reply acknowledgment for the item and each question, and private
reply images. The browser checks a 1200-pixel reply image in both conversations
at phone width and after a thread refresh. The conversation group also checks that confirmed conflicts
return local drafts to their editors, preserve saved asks, and keep reference
items outside the conversation workflow. The closed-item check closes the
questions between saving an ask and delivering it, then verifies that the
saved ask survives the conflict and the conversation stays readable.

For a screen change, also save one item of each kind, run `quarterdeck serve`,
and check the Desk, each tab of the library pop-out (try 3, 30 and 200 saved items) and each item page at phone width (about 390 px) and
desktop width: the page scrolls to its last element and back, and nothing
scrolls sideways.
