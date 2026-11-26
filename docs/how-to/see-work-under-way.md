# See work under way

Open **Under way** on the Desk to see the work in progress in your Firstmate
home. Opening the tab never runs the fleet snapshot. It shows the saved
reading that the [Workers page](read-workers.md) shares.

## Before you start

Set [`firstmate_home`](../reference/cli-and-config.md#json-configuration) in the
config, then restart `quarterdeck serve`. Without that setting, the tab says
where to set it. Rendered snapshots have no Under way tab.

## Read the list

1. Select **Under way** on the Desk. The tab says **Last checked** with the
   time of the saved reading in your local time zone. Until the first check
   finishes, the tab says so and lists nothing. `quarterdeck serve` checks the
   home every 30 minutes, and **Check workers** on the Workers page checks it
   at once.
2. Read the two groups. **In progress** lists work that is not merged, with
   **Question asked** first, then **Ready to merge**, then the rest.
   **Merged today** lists merged work below it.
3. Read each row. It shows the work's title, its project, one status word,
   and links. Merged rows have no status word. A long title is cut short
   and shows in full when you point at it. The title leaves out a project name
   at its start when the Project column shows it.
4. Select **Question** on a **Question asked** row to open the question. It
   opens the first address in the question, such as a review board, or the
   Quarterdeck conversation when the question has no address.
5. Select **Pull request** to open the pull request. Select **Conversation**
   to open the Quarterdeck conversation about the work.

A row is one ship or scout with an open terminal. A second mate has no row.
Work the home recorded as merged today also has a row until the day ends,
even when its terminal has closed. 

## What the statuses mean

The tab uses the [work status words](../reference/glossary.md#work-status-words).
It maps only what the reading states outright.

| Status | The reading reported |
| --- | --- |
| Ready to merge | The worker is done and has a pull request. |
| In review | The worker is busy and has a pull request. |
| Building | A ship is busy and has no pull request yet. |
| Merged today | The home's backlog marks the work merged on today's date. |

A scout that is busy shows **Working**, because the reading does not say a
scout builds anything. Any other state shows the Workers page word, such as
**Question asked**, **Stuck**, **Finished** or **Unknown**. A busy worker whose
pull request the reading does not name yet shows **Building**, even when its
pull request is open.

## Link to a conversation

Firstmate keeps a map from each piece of work to its Quarterdeck conversation
in `data/qd-threads/links.tsv` inside the home. Each line has two columns
separated by a tab: the work's task name, then the conversation ID such as
`Q29`. When a task appears more than once, the last line wins.

Quarterdeck only reads this file. A row has no **Conversation** link when the
file is missing or unreadable, names no such conversation, or has no line for
the task.

## If a check fails

The tab keeps the last reading and says **Check failed** with the time of that
reading. Open [Workers](read-workers.md) and select **Check workers** to try
again.
