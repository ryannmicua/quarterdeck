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
