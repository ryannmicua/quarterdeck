# Read the workers section

Open **Workers** on the served Desk to see who is running in your
Firstmate home.

## Before you start

Set [`firstmate_home`](../reference/cli-and-config.md#json-configuration) in the
config, then restart `quarterdeck serve`. Without that setting, **Workers**
shows a setup message. Rendered snapshots omit the section.

## Read the list

1. Open the served page. The section reads the home once and shows **Loading
   workers** while it waits.
2. Read each row. The name and status come first, followed by the project,
   activity, time of last activity or contact, and links when available.
   Row times use your local date and time.
3. Select **Refresh workers** to read the home again. The page never refreshes
   the list by itself. **Last refreshed** shows the time of the last successful
   read, in your local time zone.

A worker is listed while its terminal is open, and leaves the list on the next
successful refresh after the terminal closes. Second mates are listed
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

## If a refresh fails

The page keeps the last list in the tab and says **Refresh failed** with the
time of that list. If the first read fails, the page says **Workers could not
be loaded**. Select **Refresh workers** to try again. See the
[endpoint reference](../reference/cli-and-config.md#workers-endpoint) for
timeout and concurrent-read behavior.
