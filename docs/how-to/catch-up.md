# Catch up on finished work

Catch-up shows short notices that Firstmate saves when a piece of work is
finished. Read a notice, follow its link if you want, and mark it read. The
notice then leaves the Desk and is removed a few days later.

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

The command prints the notice ID, such as `C4`. Only `--title` and `--summary`
are required. The summary is one sentence of up to 300 characters.

- `--item Q3` links the notice to a saved item. The item must exist.
- `--url` and `--link-text` link to a pull request or a page. Give both or neither.
- `--project` names the project, as it does for an item.

## Read the notices on the Desk

Unread notices show on the served Desk, above the questions, newest first. Each
row has the title, one sentence, its links and the day it was saved. The
heading shows a count when more than one notice is unread. The section is absent
when nothing is unread.

- Select **Mark read** on a row to read that notice.
- Select **Mark all read** to read every notice shown.

A read notice leaves the Desk at once. If the page cannot save the change, the row
stays and the section says **The notice could not be marked read. Try again.**
If an older tab shows a notice that has since been removed, the Desk refreshes
so you can mark the remaining notices read.

Catch-up is available on the served Desk only. Mark a notice read from the
command line with `quarterdeck catch-up read C4`, or every unread one
with `quarterdeck catch-up read --all`. `quarterdeck catch-up list` prints the
unread ones.

## How long a notice stays

Unread notices stay until they are read. A read notice is removed after
`catch_up_days` days, which is 7 unless the
[default config file](../reference/cli-and-config.md#json-configuration) says otherwise.
Removal happens when a notice is next saved or marked read. The store keeps
`read_at` for each notice until then, so a count of notices read is possible
within that time. See the [store format](../reference/item-store.md#catch-up-notices).
