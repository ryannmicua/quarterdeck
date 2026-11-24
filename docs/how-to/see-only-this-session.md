# See only this session

By default Quarterdeck shows only what your current session with Firstmate
raised, touched or discussed. This holds for every page it renders or serves:
Calls, Topics, Search, the previous pages, the backlog page and the readable
pages behind them. Everything else is left off the pages, not folded away. Each of Calls, Topics
and Search starts with one quiet line, "Only this session, since <time>".

## What counts as this session

The session is the one Firstmate records with `quarterdeck board active`: it
begins at the board's `operator.session_started_at`, which
`quarterdeck board active --new-session` sets when a new chat starts (see
[Sessions](../reference/captain-board.md#sessions)). With several homes, the
newest record wins, because you are one person. An idle session (no message for
four hours) still bounds the page until the next session starts.

A task, topic or report belongs to the session when any of these happened at or
after the session began:

| Item | Belongs when |
| --- | --- |
| Board item | its `touched_at` is in the session (Firstmate raised it, or you engaged with it). |
| Backlog task | it is a session board item's task; its `Captain hold set:` time is in the session; the ledger records it as dispatched (`task.dispatched` in `state/fleet-ledger.jsonl`); it is a piece of a session topic; or you or Firstmate named its id in the chat. |
| Topic | an entry is dated in the session; a session board item names it as its topic; or the chat names `data/topics/<id>.md`. A session topic shows whole, with its state, links, open decisions and conversation. |
| Report | its task belongs to the session; a session topic links it; the chat names its id; or the report file changed during the session. |
| Captain wait, activity card, finished work | its task belongs to the session. |

"The chat" is the saved Claude Code session of each configured home (the
`transcripts` folder, by default `~/.claude/projects/<encoded home path>/`),
read since the session began: only your typed messages and Firstmate's text
replies. A task id counts only as a whole word, such as `tide-merge`.

## Links from this session

Calls lists every `http` or `https` address you or Firstmate wrote in the chat
since the session began, under **Links from this session**: pull requests,
Lavish pages, previews. Links that belong to a session item (a board item's
links, a topic's `Links:`, a task's pull request) also stay with the item.

## When no session is recorded

When no home's board has an `operator` record, Quarterdeck cannot tell where the
session begins. It shows everything, and Calls, Topics and Search say "No
session is recorded yet, so this shows everything."

## Show every session

To see all sessions' items, as before:

```sh
quarterdeck render --all-sessions
quarterdeck serve --all-sessions
quarterdeck export --artifact page.html --all-sessions
```

Or set `"session_only": false` in the [config file](../reference/cli-and-config.md#json-configuration-schema).
`quarterdeck service --all-sessions` writes the flag into the systemd unit. The
page itself has no "show everything" control.

`quarterdeck export` builds the cloud artifact from the same session scope.
`quarterdeck attention --json`, the `reports` and `calls` commands, and the
captain-wait store are not limited to the session.
