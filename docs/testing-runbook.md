# Targeted testing

## Reverse proxy path prefix

Run `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.BasePathTests`.
The pass-through contract requires prefixed pages and actions to reach their
handlers, while bare-prefix and unprefixed GET/POST requests return 308 beneath
the prefix with their queries intact. Encoded path characters must stay encoded
in redirect locations. Root serving remains unchanged without a prefix.
The URL CLI must report the installed service's explicit prefix, selected config,
or default config in that order, including a config created after installation;
text and JSON output must agree. These checks reject unprefixed content aliases
and stale root URLs. Generated units are parsed into normalized ExecStart
arguments, then passed to the CLI parser to verify the explicit prefix and the
default's omitted option; text elsewhere in the unit cannot satisfy this check.
Run `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PushNotificationTests.test_page_script_hides_the_control_on_plain_http_and_offers_it_on_https`
to execute the notification script in Node at root and under `/qd`. Recorded
fetches and worker registration must use the expected key, subscribe and
unsubscribe URLs, worker URL and scope, rejecting inactive or root-only calls.
Node is required; all browser boundaries are local stubs with no network calls.
Fixtures use disposable homes, config and state, with local
HTTP servers that clean themselves up; no real inbox writes or push delivery run.
The Caddy example preserves the browser's Host for HTTPS upstreams, as required
by the same-origin write checks; this Python test does not execute Caddy.

### Live HTTPS prefix walkthrough

Live checked 2026-11-18 with fictional home data, the real `serve` CLI,
Caddy 2.11.9 and disposable Chromium. Use unused ports (check listeners first),
private XDG directories outside the checkout and homes, and a disposable TLS
certificate. Keep these directories within the test runner's authorized evidence
location when its workspace boundary requires that. Adapt the documented Caddy
pass-through example to those local addresses, preserving Host and the query.

Open `/qd?filter=waiting&x=%2F`; verify the final `/qd/` URL retains the query.
Follow a topic, its report and the classic page; mark a report, send a report
reaction and topic question, and save a view. A saved view reloads immediately:
wait for its new view button rather than transient “Saved” text. Verify delivered
notes only in the fictional home's inbox recorder. Check the real browser's
worker URL and scope are `/qd/sw.js` and `/qd/`, and fetch the manifest, icon
and key beneath the prefix. Exercise unprefixed GET/POST redirects, encoded
paths, malformed prefixed actions, root mode and service URL/config precedence.
Capture desktop/phone screenshots and HTTP/CLI transcripts. Never subscribe a
real device. Stop and await every browser/server child and remove tools, fixture
homes and private runtime state, retaining evidence only.

## Topic input failures

Run `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.TopicTests`.
The topic-format contract requires invalid offset hours and minutes to produce
numbered errors without losing other topics, malformed URLs to warn in Links
and remain unlinked without discarding other formatting, and unavailable homes to print
problems and make the checker exit 2 even alongside readable files. The
regressions exercise file loading, the checker CLI and generated HTML, rejecting
crashes, normalized invalid offsets and false clean checks. Existing topic tests
cover valid offsets, links, backlog references and thread rendering.
All homes and outputs are disposable fictional fixtures with isolated config
and state; no real home scripts or inbox writes are needed. Fixtures clean up
automatically.

### Shared page output and topic layout

Run `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.OneLookTests test_quarterdeck.TopicTests`.
The generated-output checks cover stylesheet inclusion, interface glyphs and
machine-readable badge counts; they do not prove computed styles or layout.
For classic-page naming and stylesheet inclusion, run
`PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PhoneFixTests.test_page_uses_one_name_per_thing_and_shared_stylesheet`.
This checks generated output, not rendered muted-text contrast.
Topic tests exercise static and served pages, including decision controls, using
disposable fictional homes. They require no real inbox writes or push delivery.

For browser layout checks, use a fictional topic with a short open decision and
a long conversation, plus a closed topic opened from Search. At 1440 and 2240 px,
verify decisions and state flow together in the left column regardless of
conversation length, and the title and closed notice precede both columns.
At 375 px, verify title, closed notice when present, state, decisions and
conversation retain that reading order without horizontal overflow. Check topics
without open decisions too. These layout checks require an actual browser;
matching stylesheet source is not evidence of rendered behavior.

Live checked 2026-11-19 with the real `serve --home <fictional-home>
--no-snapshot --allow-marks` CLI and isolated Chrome. Check the front page at
375, 768, 1280, 1440 and 2240 px; classic, search, adoption, topics, exported
artifact and a filled decision brief at phone and wide widths. Count the classic
sidebar together with main content when measuring screen use. Open Activity and
expand a folded repeated-run card; checking a collapsed section misses its layout.
Search for a report, mark it, reload until the published page offers Unmark, then
unmark and reload again. A document page's immediate save response offers Undo
and Back to Quarterdeck; it does not replace its controls until reload. Follow a
topic's Markdown document link and its raw-file link, including long unbroken
text and scrollable code. Verify dark-mode overflow and normal/muted text contrast
against the 4.5:1 normal-text threshold on each page kind. Save screenshots as well
as measured browser rectangles. Keep browser dependencies/profile in disposable
workspace storage and review state in the authorized external evidence directory;
stop and await Chrome and the CLI server, then remove disposable storage.

When seeding a linked Markdown document, use `data/<task>/<file>.md`, then
follow its `doc-*.html` link. A report can instead open as `rv-*.html`; using
that route as the document fixture would miss long-text and raw-file checks.
For repeated-run cards, give the ledger task IDs the same title in the backlog;
ledger `title` fields alone do not supply the displayed title.

### Read-only topic pages in a browser

Live checked 2026-11-17 against the real CLI server with fictional homes and
isolated Chromium. Serve with `--home <fixture> --no-snapshot` (no writes enabled),
and isolate XDG config/state. State must be outside the checkout and fixture homes.
Use the published Maple Harbor example plus a second home with the same topic id,
a done topic, an empty home, and underscore/dot-prefixed drafts referenced from
both Links and conversation text.

At 375, 768 and 1280 px, follow Topics to the thread and its report link; check
state, task states, one card per open decision, settled replies and no horizontal
overflow. Check phone dark mode too. Times use the browser locale as well as its
time zone: 14:05 UTC is 10:05 PM in en-US / Asia/Manila. Append a fictional `Re:`
answer, wait beyond the 30-second server cache TTL, and reload until refreshed;
the decision leaves the pinned list and front-page count. Confirm same-id topics
in different homes open distinct pages, Closed topics remains accessible, and an
empty home has no Topics section. POST to a read-only thread must return 405.

For recovery, combine a misspelled field before valid Id/Decision fields, an
invalid time offset, a missing Piece, a hostless link, mixed code fences, an
existing Markdown link whose label contains a URL, and escaped HTML. Check the
thread preserves usable messages, code and links and lists the same problems as
`topics check`, with no duplicate missing-Piece warning. Draft document/raw routes
must return 404 while an ordinary document still opens. Missing homes, an
unreadable topics directory and a directory at `data/secondmates.md` must make
checking exit 2; an absent optional topics directory is valid. These actions are
safe only with disposable data. Restore fixture permissions, stop servers and
browser children, remove fixtures/dependencies, and retain screenshots and CLI/HTTP
transcripts in the assigned evidence directory.

## Attention feed refresh failures

Run `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.AttentionTests.test_failed_refresh_keeps_last_good_needs_you_list test_quarterdeck.AttentionTests.test_empty_cached_feed_stays_incomplete_until_refresh_recovers test_quarterdeck.AttentionTests.test_only_decisions_errors_collapse_diagnostics`.
These cover the [failed-feed refresh contract](reference/attention-json.md#failed-feed-refreshes),
including empty-cache priorities, single/all saved views, status and live-card
empty wording, and recovery. The HTML parser checks rendered text visibility,
including escaped diagnostic content.
Fixtures are fictional and disposable; no real home scripts or inboxes run.

Run `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.AttentionTests.test_cached_feed_is_scoped_to_home_and_resolved_data_directory`
for cache identity. A failed read after changing data directories must not show
the previous directory's decisions. Returning to a previously read directory,
including an equivalent resolved path, must recover only that directory's list;
a different home sharing the directory must not inherit it.

Manual CLI/server gate check (2026-11-14): use fictional homes with executable
`fm-attention.sh --json` and snapshot scripts, private XDG config/state, and feed
rows timestamped at the current time. A 17-second attention script reproduces
the old 15-second failure and must succeed with the 60-second timeout; a
65-second script must exit unavailable at approximately 60 seconds. For `serve`,
wait 31 seconds between builds to exercise the real cache TTL. Change the page
title per build to distinguish the old response from the completed refresh;
GET must return the previous page promptly during a 17-second refresh.
Then fail the feed after populated and empty successes: preserve cached items,
show unknown status, and never claim an all clear until recovery. Change the
configured data directory before failure, then test an equivalent resolved path
and a second home sharing that directory; cached decisions must not cross
identities. Capture mobile screenshots and served HTML. Review state must live
outside the checkout (the gate evidence directory is suitable); keep all home,
config and browser-profile fixtures disposable and stop every child on cleanup.

## Background cache and write responses

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.ServeTests -v`.
The cache regressions use controlled clocks and blocked builders to check stale
reads, single concurrent builds, failed-refresh cooldowns, and invalidations
arriving during a failed build. Waiting invalidations must still report their
own build failures. HTTP regressions check that marks, views, wait discards and
reactions return successful mutation results even when the following render
fails, while GET retains the previous page. These tests use fictional callbacks
and loopback servers; they do not mutate real homes or deliver inbox notes.
Servers and disposable fixtures clean up automatically.
`ServeTests.test_reaction_response_does_not_wait_for_rendering` checks call
reactions and report replies with idle and already rebuilding caches: the HTTP
response must arrive while rendering is blocked, and releasing the builder must
eventually publish updated pages. Reaction page assertions poll for that
background publication rather than requiring it before acknowledgement.

## Captain wait transcript and home boundaries

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.CaptainWaitTests -v`.
The tests use fictional disposable homes, session JSONL, helper responses and
inbox scripts; no real Firstmate home or helper is needed. Temporary fixtures
clean up automatically. Keep the temporary store outside the checkout and
fixture homes, as required by the store's privacy boundary.

Regression cases check owner questions at and during new/reset hold windows,
answers arriving after the briefing window, and briefing paragraph deduplication.
Interleaved sessions must keep replies with their own question and settle
independently; resumed UUID copies count once. A shared store must preserve
pending briefing and clarification jobs for unselected or unreadable homes,
then complete them with the right transcript once selected and readable.
Both ordinary and `--all` JSON listing must contain only selected homes.

Clarification regressions also check an unsettled session alongside a completed
one, and replies appended after an earlier answer was recorded. Reply UUIDs
deduplicate both pending jobs and completed work; scanning stays anchored at the
hold rather than moving past questions that may receive more replies. Injected
directory, metadata and file-read errors must leave jobs and scan state unchanged;
after access recovers, both briefing and clarification content must be collected.
Read failures report the affected home on stderr and leave other homes' helper
jobs runnable, regardless of home order. An absent transcript directory counts
as no saved messages. Duplicate labels and normalized key namespaces must refuse
sync, list and discard before mutation or inbox delivery, and page loading must
return no attachable wait cards with a clear error. Include registered children
and punctuation-only labels that share the default namespace.
Paragraph cases exercise both helper completion orders, repeats within a single
selection, and briefing retries that remove overlapping clarification paragraphs
or their entire empty entry while preserving the remaining detail's timestamp.

## Keyless report review marks

Last tested: 2026-10-12 with a disposable CLI server and headless Chrome.

1. Create a fictional Firstmate home with a backlog and an open report.
   `BearingsTests.make_home` in `tests/test_quarterdeck.py` supplies a fixture.
2. Set `XDG_CONFIG_HOME` to an empty disposable directory and `XDG_STATE_HOME`
   to disposable storage outside the checkout and Firstmate home. Quarterdeck
   rejects review state inside either directory. Use `--no-snapshot`.
3. Run `python3 quarterdeck.py serve --home <fixture> --no-snapshot --allow-marks`.
4. In a fresh browser, click **Mark reviewed**. Verify no dialog appears, the
   page reloads, and the report moves into **Reviewed reports**. Expand that
   section and click **Unmark**; verify the report returns to needs review.
5. Check generated `quarterdeck/review-state/marks.json` in disposable state.
   No `mark-token` file should be created.
6. Send requests without credentials: cross-origin and cross-site requests must
   return 403; a fingerprint from before a report edit must return 409. Verify
   refused requests do not change marks.
7. Restart without `--allow-marks`: buttons disappear and POST returns 405.

Only mutate fictional reports and disposable state. Stop the server and browser,
remove fixtures and browser profiles, and retain screenshots and HTTP/state
transcripts as evidence. The three focused `ServeTests` beginning with
`test_marks_endpoint_` and `test_marks_are_refused_and_hidden_unless_enabled`
cover persistence, rejected requests, stale pages, and the opt-in boundary.

## Desk relevance and private artifact round trip

Last tested: 2026-10-12; all 22 `DeskRelevanceTests` plus nine focused
CaptainBoard, Bearings and Serve caller regressions passed, including Node.

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.DeskRelevanceTests -v`.
The tests create fictional homes and disposable state, then remove them; no
real home or report is needed. Node on PATH enables the generated artifact's
executable interaction test; without it that test is skipped. The fixture DOM
and database exercise the emitted page's buttons and download API, without
requiring a cloud account or publishing work data.

Check that reviewed → Unmark → Discard → Undiscard and discarded → Undiscard →
Mark → Unmark end at needs review, both on the artifact and after import.
Downloads must omit actions at or before the snapshot time, changed-content
actions, and unknown reports. Snapshot timestamps preserve subsecond precision.
Current unreviewed report bodies must appear as readable text in the card's
detail view; stale, discarded and reviewed bodies are omitted.

The same class covers routed board/backlog/Bearings keys, missing-board call
fallback, home-scoped session matching, inverse-action interleavings and HTTP
responses for stale inverse actions. The focused tests mutate only fictional
data and disposable state; their servers stop through test cleanup.

## Recent work and shared decision visibility

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.AttentionTests -v`.
The class creates fictional homes, feed scripts, and ledger files in disposable
storage and removes them automatically. It never reads or writes a real home.

Check the three-day boundary with and without ledger coverage: a recent feed
`last_touched_at` keeps an old decision relevant, but a dated bucket stays
hidden on the page until Firstmate reports it due. Whole-page checks exercise
Captain's Call, board-owned and unsorted calls, related calls, bullet and table
home details, keyed calls, and exported board bands. Quiet decisions belong in
collapsed sections; dated decisions must be absent even with `--all`.
The CLI must reject the removed `--recent-days` option.

Board-touch regressions use `quarterdeck board touch --at` on fictional old
calls and verify shared attention, board bands, Captain's Call, and export
output. Recent touches reactivate quiet decisions, expire after three days,
and never override dated deferral. Registered secondmate boards exercise
routed and keyed identities without changing a same-named parent task.
Export error regressions cover incompatible ledger and feed versions: stdout
stays empty, existing JSON and artifact destinations stay unchanged, and the
error identifies the failing source. No Firstmate update hook is installed.

The same focused class covers un-escalated secondmate holds absent from the
parent feed: board touches, backlog dates, local and parent ledger work,
quiet calls, and a dated hold becoming due. It exercises table and bullet
captain blockers in Underway and Charted Next, their collapsed background
counts, matching home/header totals, and exported desk/parked bands.
A real CLI stale → current transition verifies attention JSON, rendered
visibility and exported stale/desk bands using disposable private state.

For snapshot-owner normalization, include the four `BearingsTests` methods
`test_snapshot_path_classifies_fields_and_enriches_from_backlog`,
`test_snapshot_secondmate_work_uses_registered_home_label`,
`test_snapshot_call_from_unregistered_secondmate_keeps_its_owner`, and
`test_secondmate_summaries_do_not_duplicate_live_work` in the same focused run.
Registered local Underway/gate rows use the child owner plus its local task
ID, while unregistered routes keep their existing owner and routed ID.

Dated-note regressions check bullet and table holds, with and without a
structured hold-until field, immediately before, at, and after the due time.
They include date-only and zoned ISO timestamps with both T and space
separators, and confirm that independent dependencies still defer the call.
Worker-only regressions check recorded activity times in attention JSON and
board/export rows under active, idle and absent operator sessions. Shared
relevance must never imply a captain message. The executable artifact test
`DeskRelevanceTests.test_artifact_counts_cards_opens_details_and_reports_each_capability_state`
checks matching labels on both the card and its detail view when Node is on PATH.

`AttentionTests.test_child_ledger_activity_is_shared_by_feed_and_fallback_decisions`
uses an absent parent ledger and an hour-old child worker event. It checks
fallback, routed, home-qualified and keyed feed representations against the
same relevance, event timestamp, page visibility and exported desk band;
a same-named parent task must remain background.

Manual CLI/browser check (2026-10-25): shift the attention fixtures' timestamps
relative to the current time before running `serve`; the CLI has no clock override.
Config home entries use `path`, not `home`. For gate runs that permit evidence
storage, put disposable `XDG_STATE_HOME` there: review state inside the checkout
is deliberately rejected. At `/`, scroll to `#attention` (and `#recent-work` when the last six hours have work), expand
`#recent-background-0`, and verify quiet calls appear while dated calls remain
absent. Capture desktop/mobile screenshots and the incompatible-ledger warning.
Stop the disposable server and remove fictional homes and private state afterward.


### Priority waiting queues and preference safety

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.AttentionTests -v`.
Fixtures are fictional disposable homes; no real home or preference file is used.
The priority regressions check quiet pending decisions with background output
both enabled and disabled, board-only project/ask metadata, merge/read queue
classification, and visible versus collapsed output on both the attention
section and board. Multi-home cases check that one home's priority work keeps
another home's unrelated work behind the counted more indicator.

CLI checks accept projects from route registry entries and pending items
without a projects directory. Set and clear must reject explicit, default,
symlinked, and separate-data-directory destinations inside selected homes.
Malformed preference elements must produce a concise CLI error while render
still succeeds. Temporary fixture/state directories clean up automatically.


The same focused attention class checks that the six-hour ledger feed is the
page's sole recent-work list in snapshot and backlog modes. Yesterday's landed
items and future ledger events must not appear; the exact six-hour boundary
is included. Across homes, sort before applying the visible-list limit so the
newest event stays visible. Qualified project names from both registry entries
and waiting items must work with set and explicit project queries. Empty project
results report unknown status when feed, ledger, home, backlog, child, registry,
or board data is unavailable, or when the feed is deliberately skipped.


Board-completeness regressions exercise real reads of malformed JSON, non-object
boards, missing or invalid item lists, skipped records, ignored project metadata,
invalid `utf-8`, and a directory at the board-file path. Run each against parent
and child homes: empty project summaries must say status unavailable, while a
valid empty board may confirm nothing is waiting. Project-query regressions use
two qualified projects sharing a short name and compare explicit query output
with saved-priority output, including previously saved short names, `--more`,
and exact-name precedence.


Priority-board regressions keep a report linked to an active task under the
collapsed Related to this session section, both with matching priorities and
with the empty-priority fallback. Child-registry failure cases cover permission
denial, a directory at the registry path, and invalid `utf-8`. Readable pending
work must survive page and snapshot generation; the failed child retains its
registry error and empty priority summaries remain unavailable. The same check
exercises project discovery used by priority writes.

Manual gate check (2026-10-25): use a fictional board with `decide`, `merge`,
and `read` items sharing a priority project, plus an unrelated decision. Serve
with isolated XDG config/state and `--no-snapshot`. At 1280px and 375px, verify
queue order and no horizontal overflow; expand the counted more toggle and
`#related` to confirm unrelated work and the linked report remain accessible.
Reload after replacing the ledger with only a seven-hour-old event: `#recent-work`
must disappear while waiting queues remain. A directory at a registered child's
`data/secondmates.md` exercises the real read-failure path without OS permission
simulation. Stop the server, close the test tab, and remove fictional homes.


### Saved briefing view regressions

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.AttentionTests -k briefing_regression -v`
for queued work in information/project sections, first-section task claiming
across recent work and queues, and saving/building Done recently. Fictional homes
and preferences are disposable. The editor regression needs Node.js on PATH
and executes the generated page scripts with DOM controls and a held save
response: both priority checkboxes must be available, and edits, moves and pending
saves must defer automatic refresh. While a save is pending, inputs, ordering
buttons and view switches must be disabled; failure restores them and success
keeps the submitted name. Deferred-decision cases exercise feed and backlog
sources, bullet/table records, routed children, both section orders and expiry:
deferred tasks stay out of information/project queues while ordinary queued work
remains visible. Recent/Done ordering cases vary priority order and activity time,
including a decision tier ahead of merged and started events. Preference cases
load padded names/projects with a lowercase startup name through CLI readers,
subsequent saves and the generated editor scripts. Reads must leave the file
unchanged. Invalid section-type cases cover arrays, objects and other non-string
JSON values through saved-preference reads, CLI errors, page fallback and the
real views POST handler. Invalid POSTs return 409 without writing preferences;
a subsequent valid save succeeds. No real home or browser state is changed.

Manual gate check (2026-10-26): serve a fictional home on loopback with isolated
XDG config/state and `--allow-marks`. Use the normal feed-enabled mode to check
populated waiting sections; `--no-snapshot` deliberately skips the attention
feed. Save a named project-first view with **Use at startup**, then check its
reloaded panel and the later waiting section's **1 shown above** at 1280px and
375px. Trigger `quarterdeckReload()` after an editor input event to verify dirty
edits survive. Delay delivery of a real save response in the browser to verify
inputs and view switches stay disabled until completion. A second server
without `--allow-marks` must refuse POST `/api/views` with 405.

For CLI ordering fixtures, include `task.dispatched` records carrying the project
before `task.merged`; use both priority orders and vary timestamps. Old backlog
captain holds without activity can contribute additional background decisions,
so count the complete fixture when checking the older-decision card. Keep all
mutations fictional, retain CLI JSON and desktop/mobile screenshots as evidence,
then stop servers, close test tabs and remove fixture/state directories.


### Activity card reconciliation

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.ActivityCardTests -v`.
Document-link regressions check child-owned cards in all waiting queues, with
and without board entries, against different parent/child documents at the same
relative path. Parent-board items routed to child tasks are built through
`board_view()` to preserve the distinction between board home and task owner.
An unavailable child directory must never select the parent's
file. Colliding home-label namespaces must register no document links for the
ambiguous cards, regardless of home order, board presence or remote-home status;
unambiguous homes must retain their links in initial markup and polling payloads.
Initial card markup and polling payloads must link references in current
and earlier briefing paragraphs, clarification questions and board recommendations.
Manual document-link check: serve fictional homes with isolated XDG config/state
and `--no-snapshot`; seed saved wait cards in a disposable store outside the
checkout and homes. At 375px, open a card and follow both its Markdown report
and bare `explainer.html` links. Check current/earlier briefing and question
references, then edit the board and wait for cache refresh plus card polling.
Also run `render --output <disposable-output>/index.html` and follow its links.
Use same-path parent/child reports with distinct headings to check ownership;
make the child unavailable and confirm no parent fallback. An HTML explainer's
script should run, but its sandbox must block storage and write-endpoint access.
Each opened document must show the bar that links back to Quarterdeck, and Open file by
itself must return the unmodified file at 375px and desktop width.
Scroll a long HTML document to its final control and confirm it clears the bar,
including after resizing so the links wrap. The focused navigation regressions
are `ActivityCardTests.test_document_bar_reserves_its_height_after_wrapping`,
`ActivityCardTests.test_rendered_document_navigation_uses_the_main_filename`, and
`ActivityCardTests.test_standalone_report_has_no_site_back_link`. They execute the
generated spacer script with supplied browser geometry, render default/custom/Lavish
sites and check their emitted back-link destinations, and run standalone report
output. The geometry check covers resize handling, not browser layout; use the
manual scroll check above for layout. The required destinations and unobscured
final control come from the navigation contract; stale `index.html` links, a
fixed-height spacer, and a standalone back link must fail these checks.
Retain screenshots and HTTP output; stop servers, close test tabs and remove
fictional homes/state afterward.
The fixtures use disposable fictional homes; set `TMPDIR` to a directory inside
the worktree when file writes must stay within it. Node.js on PATH executes the
generated polling script against DOM controls and scripted fetch responses.
Checks cover drafts, focus, selected reactions and pending sends during removal,
replacement and section changes; idle cards reorder and move without losing open
details, and an empty payload keeps a busy card visible until it becomes idle.
Python cases check timestamp revisions, stable desk links after renumbering,
uncapped section counts, and ledger/backlog completions from registered homes.
The executable check also exercises desk navigation to present and missing
targets, including guarded reloads during replies, sends and view edits.
Observer cases cover below-half visibility, interrupted dwell time, hidden tabs,
returning to the tab, explicit opening, and removing or replacing a timed card.
The DOM harness queues toggle events when open state changes: restoring an
offscreen expanded card must leave its new revision unseen, while closing and
reopening it explicitly acknowledges that revision. Reaction cases cross the
one-hour age boundary with reply controls enabled and disabled; unchanged
records keep their revision, while changed text, choice, source or absolute
timestamp and reaction removal change it.
The harness checks the header and Needs-you-heading unread counts on arrival and
acknowledgement, and checks that arrival, acknowledgement, replacement and
removal preserve the page title. Seen persistence is asserted through executed
localStorage writes.
No real home data or reaction endpoint is modified. Temporary homes clean up
automatically; remove the empty TMPDIR directory after the run.

Manual gate check (2026-10-26): serve `AttentionTests.make_home` with fixture
feed and ledger timestamps shifted to the current time. Board-backed calls also
need open backlog rows; otherwise the board correctly treats them as settled.
Use disposable XDG config and state outside the checkout/home, and only a
fictional `fm-inbox.sh note` receiver for reaction delivery. Observe an actual
15-second polling cycle through a background rebuild and the subsequent poll
that receives the new cards; see the [serving guide](how-to/serve-the-page.md#run-it)
for cache timing. Keep a real reply draft
open when proving polling independently of the normal 60-second page reload.
Check an expanded card updated offscreen remains New, then scroll it to about
10% visibility for longer than 1.2 seconds. For native hidden-tab timing in
headless Chrome, tab selection may leave every page visible: CDP
`Browser.setWindowBounds` with `windowState: minimized` produces a real
`document.hidden` transition; restore `normal` and require a fresh dwell.
Capture desktop/mobile details and Done recently screenshots. Stop the isolated
server/browser and remove fictional data, profile and private state afterward.

Manual cache gate check (2026-11-13): run the real `serve --allow-marks`
CLI against a fictional home with 340 reports, isolated XDG config/state, and
snapshot/attention scripts delayed six/one seconds. Wait 31 seconds after build
completion, change the configured page title, and request `/`, a linked report
and `/cards.json` concurrently. Old pages must return during exactly one build;
the changed title must appear after completion. Allow for TCP accept delays in
large connection bursts; compare latency against the deliberately slow build.
Send call reactions and report replies with idle and rebuilding caches: delivery
must acknowledge before rendering finishes. Capture the actual browser's loaded
page and **Sent to Firstmate** state. To exercise render failure after real writes,
a positive but overflowing `captain_waits.window_before_minutes` passes config
validation and fails during rendering; marks, views and reactions must still
persist and return 200. For wait discard, have the fictional inbox receiver set
that fault only after accepting its note. Cached GETs must remain usable without
per-request retries, retry after the TTL, and recover after valid config returns.
Inspect persisted SQLite/preferences/wait state and the fictional inbox log.
Stop server/browser children, wait for helper completion, and remove disposable
homes, profiles and private state. Keep only synthetic screenshots and transcripts.


## Adoption history and visits

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.AdoptionTests -v`.
The fictional fixtures use disposable homes, private SQLite state, visit logs
and loopback HTTP servers; cleanup is automatic and no inbox messages are sent.
The regression oracles are the agreed answer fraction (two Quarterdeck replies
and one chat decision gives 67%), a worked median (30 and 20 minutes gives 25),
and historical weeks that retain a review after discard, unmark and file edits.
Mutable pre-existing state must not be backfilled. Repeated observations must
not move arrival dates. Read-only page builds must not start collection.
The HTTP regression advances a clock across midnight and beyond the visit
window: automatic requests add no visits, while later manual navigation does.
The generated reload JavaScript is executed with Node to check the marker,
visible URL cleanup and preservation of unrelated query parameters and hashes.
Device cases include Android phones, Android tablets and iPads with Mobile in
their user agent. Node is optional for the Python tests but required for the
reload-script check; a skip means that browser behavior remains unverified.

Chat boundary cases use Asia/Manila: Sunday 18:00 UTC belongs to Monday,
while a bare Sunday stays Sunday. Artifact-import regressions run both action
formats, apply Sunday actions on Monday, and require Sunday event times without
backfilling pre-collection actions. Pending-card activity updates must preserve
arrival counts and waits; distinct captain-hold identities remain distinct.

## Quick win status, navigation and recurring work regressions

Run `PYTHONPATH=tests python3 -m unittest test_quarterdeck.QuickWinTests -v`.
Q1 is the oracle for counting active needs across priority-scoped waiting lists
and homes, deduplicating keys and excluding quiet/stale background decisions.
The status must also withhold an all-clear when a healthy feed accompanies an
unreadable registry, child backlog or captain board, or when feed reading is
skipped. Its Needs-you destination must include active items held in waiting_more;
page rendering and live responses share the same card builder. Informational
cards remain scoped to priority projects. Oldest age uses the request opening
time even after a recent touch, across feed decisions, needs-you classifications,
board-only filed dates and priority scoping; unknown opening times omit the age.
Q3 requires links to the rendered report lists, related items and background
sections. Q8 requires separate recurring cards and identities per project for
both started and completed work. These cases reject a false all-clear, omitted
navigation and cross-project folding. Fixtures are fictional and disposable;
no real homes or inbox deliveries are used. No cleanup is needed.

The focused class checks generated markup, not computed CSS. To verify Q1,
Q3, Q4 and Q6 visually, render fictional dashboard and report pages in a browser
at phone and desktop widths. Scroll the dashboard to check sticky navigation;
on a phone, confirm the relative update timestamp replaces the long timestamps.
Confirm the report header title is visible, tap targets measure at least 44 px,
and the four reaction choices occupy two rows and two columns at both widths.
A later overriding CSS rule must be judged by those rendered results; source
substring assertions cannot establish these properties.

## Reply drafts and Unicode limits

Run the focused reply regressions with Python and Node:

```sh
PYTHONPATH=tests python3 -m unittest -v \
  test_quarterdeck.DeskRelevanceTests.test_reply_box_grows_counts_refuses_a_too_long_reply_and_keeps_a_draft_across_reloads \
  test_quarterdeck.DeskRelevanceTests.test_a_twenty_thousand_character_reply_is_delivered_whole_and_the_page_offers_a_text_area \
  test_quarterdeck.DeskRelevanceTests.test_artifact_sends_reactions_to_the_inbox_collection_and_opens_the_composer
```

The emitted page scripts execute against disposable browser-boundary doubles.
The draft contract requires restoration in card, desk and arriving composers,
expiry after seven days, retaining drafts on failed sends, clearing every copy
of the sent item and kind, and allowing refresh for saved, unfocused replies.
Blocked or full browser storage must defer refresh for unfocused edited text;
a later successful save must release that guard. Edits mirror in both directions
between card, desk and arriving composers for the same item and kind, including
when storage writes fail. Reload checks the current stored text: deleting or
overwriting that draft, or making storage unreadable, must preserve page text.
Arriving composers inherit unsaved text from an existing composer. A delayed
successful send clears only text unchanged since submission: edits made from
another composer while sending must survive in every mirrored box and storage.
The regression covers card, desk and arriving senders, whitespace-only edits,
new composers during sends, and an independently overwritten stored draft.
The independent length oracle is Python's 20,000-code-point server limit:
19,999 ASCII characters plus one emoji must send whole; 20,000 ASCII characters
plus one emoji must be refused. Both page and artifact counters follow it.
Fixtures and inbox receivers are fictional, cleanup is automatic, and these
checks do not send messages to real homes.

For opening-time order across both renderers and live empty states, run:

```sh
PYTHONPATH=tests python3 -m unittest -v \
  test_quarterdeck.ActivityCardTests.test_needs_you_cards_come_first_newest_wait_first_and_the_rest_newest_first \
  test_quarterdeck.ActivityCardTests.test_needs_you_order_uses_newest_parsed_opening_times_with_activity_fallback \
  test_quarterdeck.ActivityCardTests.test_polling_preserves_busy_cards_and_reconciles_idle_cards \
  test_quarterdeck.FrontPageTests.test_needs_you_orders_decisions_waits_and_loose_cards_together_newest_first
```

The ordering oracle is newest opening first, even after recent activity;
missing or invalid openings fall back to activity, with unknown times last.
Offset timestamps compare as instants, and equal times retain their input order.
Initial HTML and cards JSON must agree,
including decisions and other needs-you items from priority and overflow lists.
The topic front page interleaves decisions, waits and loose cards in both static and
served modes. Waits use the latest dated topic entry because their state lines have
no start timestamp. Equal times retain decisions, then waits, then loose cards in
their existing order; unknown times come last.
Polling without completeness evidence must replace an old all-clear with a
neutral empty message, while preserving active replies during reconciliation.

For live ordering checks, use a fictional home with executable `fm-attention.sh`
and `fm-bearings-snapshot.sh` JSON fixtures, then run both `render` and `serve`.
Keep generated output and XDG state outside the checkout (the CLI enforces this);
the gate evidence directory is suitable during an isolated test phase. Check the
served `cards.json` HTML fragments as well as the visible list. An undated feed
decision without recent activity is a background decision and does not enter the
Needs you feed; use an undated topic wait to exercise unknown timestamps live.
Stop the server and disposable browser, then remove the fictional home/profile.


Live browser recipe for this flow: serve the fictional attention fixture with
isolated XDG config/state and `--allow-marks`, shifting fixture timestamps to
now so the six-hour activity window remains populated. Use a named browser
session with a disposable profile. At 375px and desktop width, check that
Needs you comes first and the Activity jump link opens the collapsed section.
For a delayed-send check, delay the fictional inbox for three seconds (holding
it indefinitely hits the delivery timeout); edit the matching desk composer
while the card send is pending and verify the later text survives success.
To check the poll empty state, start with a feed-only decision and valid empty
backlog/board, then corrupt the fictional feed. Allow the 30-second site cache
and 15-second card poll to run before the 60-second page reload; verify neutral
empty wording on the original page. Stop browser/server children and remove
fictional homes, profiles and state after capturing screenshots and responses.

## Answered decision cards

For validation confined to a worktree, run an unchanged disposable copy of `quarterdeck.py` and `templates/` in
a child directory, with fictional homes and private XDG state in sibling directories. Review state is
intentionally refused beneath the directory containing the running `quarterdeck.py`; putting test `TMPDIR`
beneath the original checkout therefore requires running copied tests, scripts and documentation examples beside
that disposable copy too. Remove the copy and all private state after stopping its server and browser.

Run `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.TopicAskTests`. These cover the folded line after an
answer (as a reload or another device sees it), the cut to about 80 characters with the whole answer in the
disclosure, the uncounted line under Needs you, its removal once a `Re:` names the decision, a failed delivery that
leaves the card open and counted.

The in-page fold runs in the browser only. Serve a fictional home with `--allow-marks --no-snapshot` and a fake
`bin/fm-inbox.sh`, at 390 and 1280 px: tap an option and send a typed answer over 80 characters; after the
five-second Undo each card folds, the Needs you or Open decisions count and the tab title drop by one, and a reload
shows the same lines. Serve again with an inbox that exits non-zero: the card stays open with "Not sent" and
its option buttons and **Answer in words** controls. Once the inbox works, tap an option again or resend the typed
answer with **Send**; after the five-second Undo it delivers. Append a fictional `Re:` and reload past the
30-second cache. The first request can serve the cached page while a background rebuild runs; reload again once
that rebuild completes and verify the line is gone. Check that the page scrolls to its end and back with no
sideways overflow.

## Refresh transfer and report Undo

Run the focused Python/Node regressions:

```sh
PYTHONPATH=tests python3 -m unittest -v \
  test_quarterdeck.PhoneFixTests.test_failed_fetch_keeps_the_page_and_says_how_old_it_is \
  test_quarterdeck.PhoneFixTests.test_undo_posts_the_reverse_action_and_a_failed_refresh_keeps_the_page \
  test_quarterdeck.DeskRelevanceTests.test_undo_restores_report_to_needs_review
```

Q12 supplies the oracle: network and body-transfer failures retain the last
page; a complete response replaces it without a second request. Edits, reaction
choices, sends, view saves and Undo begun during transfer postpone replacement.
Q17 requires both Mark reviewed and Discard to undo to needs review. Node checks
reverse-action dispatch, expiry cancellation while Undo is pending, a fresh
expiry window after a failed Undo that releases refresh, and retry; disposable CLI fixtures check persisted report state independently.
No real homes or inbox deliveries are used; fixtures clean up automatically.
Replacement reuses the window: global script bindings must allow reexecution,
and the replacement event stops outgoing page timers and card observations.

For refresh races with mutations, run
`PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PhoneFixTests.test_refresh_waits_for_report_and_wait_mutations_to_settle`.
The generated refresh, report and wait handlers run together with delayed GET
bodies and POST responses. Every report action, both Undo actions and wait
discard must retain the page while pending, then release refresh on success,
HTTP failure or network failure. Mark and discard must show Undo on the retained
row; permanently disabled saved buttons must not prevent later refreshes.

For report-action serialization, run
`PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PhoneFixTests.test_report_actions_are_serialized_and_failures_restore_controls`.
The Q17 oracle requires Undo to reverse the one action that actually saved.
Delayed report requests must disable competing controls for the same report,
including duplicate and newly arriving representations, while other reports
remain usable. Network and HTTP failures restore only previously enabled
controls. Failed Undo must keep the saved actions disabled and remain retryable;
successful reader Undo must restore the original actions. Both dashboard and
reader scripts execute against fictional browser-boundary doubles.


## Stated answers and delivery retries

Run the focused regressions with Python and Node on PATH:

```sh
PYTHONPATH=tests python3 -m unittest -v \
  test_quarterdeck.DeskRelevanceTests.test_answer_phrases_come_only_from_what_the_call_states \
  test_quarterdeck.DeskRelevanceTests.test_a_call_that_states_its_answers_gets_buttons_beside_the_reply_box \
  test_quarterdeck.DeskRelevanceTests.test_reply_box_grows_counts_refuses_a_too_long_reply_and_keeps_a_draft_across_reloads
```

The oracle is the call's literal stated answers: paired quotes preserve internal
apostrophes (including inside single quotes), and later sentences and recommendation
fields supply no answer text. Unquoted contractions must not become quote spans.
Directives start the text or a new line; prohibited or conditional directives
supply no buttons. Only immediately listed quoted alternatives are offered,
so later warnings containing quotes cannot add answers. Slashes in branch names
remain literal answer text; only whitespace-surrounded slashes separate
alternatives in both quoted and unquoted lists.
The generated page's executable submission handler must retain delivery IDs for
unchanged failed retries and change them whenever kind, item, reaction or reply
changes, including transitions between answer buttons and ordinary Send. Editing
mirrored drafts, toggling quick reactions, or clearing drafts after a sibling send
must preserve the ID of a failed answer whose payload remains unchanged.
These use fictional fixtures and browser boundary doubles; no real inbox sends
or manual cleanup are needed.

For a live browser check, run `serve --no-snapshot --allow-marks` against a
fictional home and a deduplicating `bin/fm-inbox.sh note` endpoint. Use a separate
read-only server to verify the reaction POST is refused without `--allow-marks`.
Keep the home, config and browser profile isolated. Review state must be outside
both the checkout and selected homes; a gate's dedicated evidence directory can
hold the disposable state database.

Open the Needs-you card, desk item and related cards, then click their stated
answers and inspect the inbox notes. Preserve a typed draft and selected quick
reaction while clicking an answer. Have the inbox save a note but return exit 3;
retry after editing mirrored drafts, toggling reactions and sending from a
sibling composer. Identical answer payloads must retain their request ID, while
changed answers and transitions to/from ordinary Send must get distinct IDs.
Capture the rendered desktop and phone composers, then stop both servers and
the browser and remove the disposable setup.


## Phone push lifecycle

Run `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PushNotificationTests`
with Python, optional `cryptography`, and Node on PATH. Without Node, the three
JavaScript tests skip; the remaining notification tests still run when
`cryptography` is available. Fixtures use invented
homes, device keys and push endpoints; all delivery is intercepted and temporary
files clean themselves up. No real subscriptions or home writes are needed.

The approved phone contract is the oracle: a subscribed server checks once a
minute without browser requests, initial incomplete reads cannot establish a
baseline, fresh backlog holds have fresh linked identities without a wait
watcher, and transient failures retry until at least one service accepts them.
Repeated successful observations send nothing, even after a year away and a
server restart; a new hold identity can still alert. The served worker executes in
Node to verify display, tagged withdrawal, and navigation to Quarterdeck;
the page script also executes with delayed worker readiness and the real push-key
response: only server-confirmed withdrawn tags close, while newer, active and
untagged notifications survive stale page snapshots and partial observations.
Apple subscriptions receive no withdrawal push. These checks reject lost
messages after transient errors, startup announcements after partial reads,
unrelated notification closure, and browser-dependent observation.

Manual isolated CLI/browser check (2026-11-17): use a fictional home with
`fm-attention.sh --json` and `fm-bearings-snapshot.sh --json` reading mutable
synthetic JSON, plus a backlog carrying explicit hold-set timestamps. Start the
real `serve --allow-marks` command with private XDG config/state, loopback TLS,
and no optional wait watcher. Review state must be outside the checkout and
fixture home; a gate's dedicated evidence directory can hold it temporarily.

For offline transport verification, route the server's `https_proxy` to a
loopback CONNECT/TLS receiver and trust its disposable certificate through
`SSL_CERT_FILE`. Use an invented allowlisted browser-service endpoint and device
keys, decrypt the received RFC 8291 bodies, and record delivery status, generic
payload and link. Keep the endpoint entirely local through the proxy; never
register an operator's phone or use real subscriptions. Wait through the real
60-second observer intervals without page requests: initial items stay quiet,
new items alert once, answers withdraw, same-hold returns stay quiet, fresh
backlog holds alert, and restart retains history. Return 503 and 429 to check
retries; start with a malformed feed and later recover it to check baseline and
withdrawal completeness. Separately run read-only and `--allow-marks
--no-snapshot` servers: controls, worker/key routes and subscription writes must
be unavailable, with no push-tracking files.

Use a disposable Chromium profile, restrict external name resolution, grant
notification permission through CDP, and open the real TLS page. Capture the
phone-sized controls, then use `ServiceWorker.deliverPushMessage` to exercise
the served worker and inspect actual `getNotifications()` results. For a trusted
click, run Chrome under a disposable `dbus-run-session` with a local
`org.freedesktop.Notifications` receiver and emit `ActionInvoked(id, "default")`
for its received notification. Verify the tab navigates to the card link. A
script-created `NotificationEvent` lacks the trusted interaction and did not
navigate; do not count it as click evidence. This validates the local Chromium surface, not Android/iOS permission
flows or vendor delivery. Stop and await the server, browser and local receiver;
remove private state, profiles and fixture homes, retaining only evidence.

Regression reproduced in the initial gate: after a complete baseline and a successful
new-item alert, remove the held backlog rows while making the attention script
return malformed JSON. The retained last-good decisions lose their backlog
hold stamps and are announced again under new tags, including baseline items.
A partial read must not turn existing decisions into fresh arrivals. Check the
received payload sequence, not only that no withdrawal was sent.

Live recheck (2026-11-17): the real TLS CLI server and local encrypted-push
receiver passed the missing-hold regression across two independent 60-second
polls. No new or withdrawal messages arrived, stored lifecycle history stayed
unchanged, and the key API authorized no cleanup. A separate disposable
Chromium/DBus run verified actual notification display, trusted click navigation,
matching-tag withdrawal, newer-tag preservation and answered-item page cleanup.
These checks establish local behavior with synthetic subscriptions, not vendor
delivery or physical Android/iPhone permission flows.

Regression check (2026-11-17):
`PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PushNotificationTests.test_failed_feed_and_missing_backlog_holds_do_not_reannounce_cached_decisions`
reproduced duplicate pushes before the fix and passes after it. It runs the
fictional home's attention script, replaces its output with malformed JSON,
and removes backlog holds. Unknown notification identities now wait for a
complete observation; incomplete polls preserve stored history, and recovery
announces a genuinely new item once without repeating existing items. The
fixture and intercepted encrypted deliveries clean themselves up.

## Report reference routing

Run the focused report-link checks with fictional temporary homes:

```sh
PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ActivityCardTests test_quarterdeck.TopicTests test_quarterdeck.BacklogFreeTextPathTests
```

The report access contract requires scout pages to refuse HTML beside escaped
Markdown, while honoring configured data roots. Activity cards and topic
conversation entries must retain links for bare report filenames beside a named
document. Both bearings and activity cards must resolve hold origins through the
origin task's explicit report directory. Topic Links for either report format
must open the shared report view, with HTML first and Markdown still available.
These checks create disposable fixtures only; they do not touch real homes.
