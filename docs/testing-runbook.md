# Targeted testing

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
Each opened document must show the Back to Quarterdeck bar, and Open file by
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

For opening-time order and live empty states, run:

```sh
PYTHONPATH=tests python3 -m unittest -v \
  test_quarterdeck.ActivityCardTests.test_needs_you_cards_come_first_oldest_wait_first_and_the_rest_newest_first \
  test_quarterdeck.ActivityCardTests.test_needs_you_order_uses_parsed_opening_times_with_activity_fallback \
  test_quarterdeck.ActivityCardTests.test_polling_preserves_busy_cards_and_reconciles_idle_cards
```

The ordering oracle is elapsed wait since opening, even after recent activity;
missing or invalid openings fall back to activity, with unknown times last.
Offset timestamps compare as instants. Initial HTML and cards JSON must agree,
including decisions and other needs-you items from priority and overflow lists.
Polling without completeness evidence must replace an old all-clear with a
neutral empty message, while preserving active replies during reconciliation.


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
reverse-action dispatch and expiry cancellation during a failed Undo, including
retry; disposable CLI fixtures check persisted report state independently.
No real homes or inbox deliveries are used; fixtures clean up automatically.
Replacement reuses the window: global script bindings must allow reexecution,
and the replacement event stops outgoing page timers and card observations.
