# Targeted testing

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
No real home data or reaction endpoint is modified. Temporary homes clean up
automatically; remove the empty TMPDIR directory after the run.
