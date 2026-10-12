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
