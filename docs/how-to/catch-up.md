# Catch up on finished work

Catch-up shows short notices that Firstmate saves when a piece of work is
finished. Read a notice, follow its link if you want, and mark it read. The
notice then leaves the Desk.

Quarterdeck never creates a notice by itself. A notice appears only when you or
Firstmate saves it with `quarterdeck catch-up add`.

## Save a notice

```sh
quarterdeck catch-up add \
  --title "Tide review is ready" \
  --summary "The tide chart review is published and saved as a reference." \
  --item Q3 \
  --url https://example.com/review --link-text "Open the review"
```

Only `--title` and `--summary` are required. Give the notice a short title and
summarize the result in one sentence. The command prints a notice ID such as
`C4`. See
[notice field limits](../reference/item-store.md#catch-up-notices).

- `--item Q3` links the notice to a saved item. The item must exist.
- `--url` and `--link-text` link to a pull request or a page. Give both or neither.
- `--project` names the project, as it does for an item.

## Read the notices on the Desk

Unread notices show on the served Desk, above the questions, newest first.
Each row shows the title, summary, saved date, and any links or project name.
The heading shows a count when more than one notice is unread. The section is
absent when nothing is unread.

- Select **Mark read** on a row to read that notice.
- Select **Mark all read** to read every notice shown.

A read notice leaves the Desk at once. If the page cannot confirm the change,
the rows and any unsent draft stay in place. Catch-up says **A notice may
already be read. Reload the page when ready.**

To review or acknowledge notices from a terminal, see the
[`catch-up` commands](../reference/cli-and-config.md#items).

## How long a notice stays

Unread notices stay until they are read. When you save a notice or successfully
acknowledge a notice, Quarterdeck removes any read notices whose retention
period has elapsed since they were first marked read. If the CLI and the Desk
use different periods, a write using the shorter period can remove a notice
sooner. See [how each process loads `catch_up_days`](../reference/cli-and-config.md#json-configuration).
