# Targeted testing

Run one group while changing it, then the whole suite before a pull request
(see `CONTRIBUTING.md`):

| Area | Command |
| --- | --- |
| Item commands and the store | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ItemCommandTests` |
| Page content | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PageTests` |
| `render` | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.RenderTests` |
| `serve`, base path and host checks | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ServeTests` |
| Workers snapshot and refresh | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.WorkersTests` |
| `service`, `url` and `help` | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.ServiceAndHelpTests` |
| Saved item commit guard | `PYTHONPATH=tests python3 -m unittest -v test_quarterdeck.PrivacyGuardTests.test_item_store_is_ignored_and_force_staging_is_rejected` |

Tests use private `XDG_STATE_HOME` and `XDG_CONFIG_HOME` directories and
invented items, and start local HTTP servers that shut themselves down.
The serve group uses headless `google-chrome` when available to verify bulk
suggestions, undo, and that conflicts and validation errors preserve drafts while the other
answers in the batch are sent. The browser checks also cover retrying a reopened
answer, exact answer times in a fixed locale and time zone, and sending suggestions
in place while preserving their text.

The workers browser test uses headless `google-chrome` to count actual snapshot
requests on entry, during a virtual minute of idle time, and after a refresh click.

For a screen change, also save one item of each kind, run `quarterdeck serve`,
and check the Saved page and each item page at phone width (about 390 px) and
desktop width: the page scrolls to its last element and back, and nothing
scrolls sideways.
