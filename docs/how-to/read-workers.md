# Read the workers section

The served page lists the workers running in your Firstmate home, under
**Workers**, between **My desk** and **References**. Workers are the one thing
Quarterdeck shows without being saved by hand. Everything else on the page is
still an item you saved.

## Before you start

Set `firstmate_home` to the home's absolute path in the
[config](../reference/cli-and-config.md#json-configuration), then restart
`quarterdeck serve`. Without it, **Workers** says so and shows nothing. The
section only appears on the served page, not in a rendered snapshot.

## Read the list

1. Open the served page. The section reads the home once and shows **Loading
   workers** while it waits. A busy home can take around ten seconds.
2. Read each row. The name and status come first, then the project, what the
   worker is doing, when it last reported and any links.
3. Select **Refresh workers** to read the home again. The page never refreshes
   the list by itself. **Last refreshed** shows the time of the last successful
   read, in your local time zone.

A worker is listed while its terminal is open, and leaves the list on the next
refresh after the terminal closes. Second mates are listed separately, under
**Second mates**, whether or not they have a terminal.

## What the statuses mean

| Status | The home reported |
| --- | --- |
| Working | The worker is busy. |
| Question asked | The worker has a question waiting for a ruling. |
| Stuck | The worker is blocked or failed. |
| Finished | The worker reported it is done and its terminal is still open. |
| Unreachable | A second mate's terminal is not running. |
| Unknown | The home gave no clear state, such as a paused worker or an old reading. |

Quarterdeck never guesses a status from silence. A row shows **Activity
unavailable** when the home names no task for the worker.

## Follow a link

A row links to the worker's pull request when the home names one. A worker
with **Question asked** also links to **Saved question** when a questions item
saved for that worker still has an open question. Save it with the worker's name:

```sh
quarterdeck add --kind questions --worker Wren --title "Map scale" \
  --description "Which markers the trail map shows." --body-file questions.json
```

## If a refresh fails

The page keeps the last list and says **Refresh failed** with the time of that
list. Select **Refresh workers** to try again. Quarterdeck waits up to a minute
for the home before it gives up.
