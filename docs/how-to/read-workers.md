# Read the Workers page

Open **Workers** from the served Desk to see who is running in your
Firstmate home. The Desk itself never reads the home.

## Before you start

Set [`firstmate_home`](../reference/cli-and-config.md#json-configuration) in the
config, then restart `quarterdeck serve`. Without that setting, the Workers
page shows a setup message. Rendered snapshots have no Workers page.

## Read the list

1. Select **Workers** on the Desk. The page shows the last reading with
   **Last checked** and its time in your local time zone.
2. After a successful saved-reading lookup, the page asks the server to check
   if there is no saved reading or the last successful check was more than an
   hour ago. The server can reuse a fresh reading from another visitor's check.
   A lookup error appears in the status line.
3. Select **Check workers** to read the home again once the current request
   finishes. The page checks automatically only on entry.
4. Read each row. The name and status come first, followed by the project,
   activity, time of last activity or contact, and links when available.
   Row times use your local date and time.

The server holds the last reading in memory, so every visitor sees the same one.
It is lost when `quarterdeck serve` stops, so the first visit after a restart
reads the home.

A worker is listed while its terminal is open, and leaves the list on the next
successful check after the terminal closes. Second mates are listed
separately under **Second mates**, even when they have no terminal.

## What the statuses mean

| Status | The home reported |
| --- | --- |
| Working | The worker is busy. |
| Question asked | The worker has a question waiting for a ruling. |
| Stuck | The worker is blocked or failed. |
| Finished | The worker reported it is done and its terminal is still open. |
| Unreachable | A second mate's terminal is closed or its agent is reported dead. |
| Unknown | The home gave no clear state, such as a paused worker or an old reading. |

**Unreachable** takes precedence for second mates. Otherwise, an open decision
takes precedence over the reported state. A row shows **Activity
unavailable** when the home names no task for the worker. The snapshot has
no status for pending checks. **Reached** shows the time of the current reading
if it confirms contact with the second mate. Otherwise the row shows
**Contact unavailable**.

## Follow a link

A row links to the worker's pull request when the snapshot supplies its URL.

## Known limits

If the snapshot lists a second mate with both a closed and a live terminal,
the row can show **Unreachable** when the closed entry comes first. See the
[endpoint reference](../reference/cli-and-config.md#workers-endpoint) for
how duplicate rows are selected.

## If a check fails

If another check is running when you open Workers, the page reports it and
keeps any saved roster and its time visible. **Check workers** is available
once the entry request finishes.

The page keeps the last list in the tab and says **Check failed** with the
time of that list. If there is no list, the page says **Workers could not
be loaded**. Select **Check workers** to try again. See the
[endpoint reference](../reference/cli-and-config.md#workers-endpoint) for
timeout and concurrent-read behavior.
