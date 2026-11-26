# Catch up on finished work

Catch-up records short notices that you or Firstmate explicitly save about
finished work. Each unread notice adds one row to **Needs you** on the Desk.

## Save a notice

```sh
quarterdeck catch-up add \
  --title "Tide review is ready" \
  --summary "The tide chart review is published and saved as a reference." \
  --item Q3 \
  --url https://example.com/review --link-text "Open the review"
```

Only `--title` and `--summary` are required. Give the notice a short title and
summarize the result in one sentence. See
[notice field limits](../reference/item-store.md#catch-up-notices).

- `--item Q3` links the notice to a saved item. The item must exist.
- `--url` and `--link-text` link to a pull request or a page. Give both or neither.
- `--project` names the project, as it does for an item.

## Read the notices on the Desk

Open **Needs you** to see each unread notice's summary, project and saved
links. If the linked Quarterdeck item is removed, its link disappears and any
external link remains.
Select **Mark read** to remove that notice from the table and update the tab
count. When there are several notices, **Mark all notices read** acknowledges
the notices in the table. Questions, replies and failed deliveries stay in
the table. If Quarterdeck reports that a notice may already be read, reload
the Desk to see the current list.

## Read the notices from the CLI

Run `quarterdeck catch-up list` to see unread notices, newest first.
Run `quarterdeck catch-up read C4` to print a notice’s details and mark it
read, or use `quarterdeck catch-up read --all` for every unread notice.
See the [command reference](../reference/cli-and-config.md#items).

## How long a notice stays

Unread notices stay until they are read. When you save a notice or successfully
acknowledge a notice, Quarterdeck removes any read notices whose retention
period has elapsed since they were first marked read. If the CLI and the server
use different periods, a write using the shorter period can remove a notice
sooner. See [how each process loads `catch_up_days`](../reference/cli-and-config.md#json-configuration).
