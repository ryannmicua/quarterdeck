# See only this session

Quarterdeck shows the topics, tasks, reports and links from your current
conversation with Firstmate. Calls, Topics, Search, the previous views, readable
documents and exports use the same session. The session stays visible when idle
until a newer operator record selects another conversation. A resumed chat
keeps work attributed to its conversation ID.

## Record the conversation

Have Firstmate run `quarterdeck board active --session-id ID` in the home where
the conversation is saved. Use the Claude Code conversation ID, the
`session_id` supplied to Claude Code hooks and the `sessionId` in its saved
messages. The newest operator record selects the conversation and its home.
Quarterdeck shows an empty view with a short explanation when it cannot
identify one current conversation.

## What appears

- A topic appears when the chat names its title, ID or
  `data/topics/<id>.md`, or when a board item touched in this session names it.
  The topic includes its entries, open decisions, pieces and links.
- A task appears when the chat names its ID, it is a piece of a session topic,
  or a board touch, dispatch, hold or captain action carries the current
  conversation ID. Use `--session-id ID` on `board touch` and
  `board upsert --touch`. Fleet ledger events can carry `session_id` and a
  timed captain hold can carry the backlog field `(hold-session-id: ID)`.
- Reports appear when named in the chat or linked to an in-scope task or topic.
  A report file's modification time alone does not make it part of the session.
- Captain waits, activity cards, finished work and saved views follow the same
  task membership. Wait briefings and clarifications must belong to the
  current hold and conversation.

Conversation evidence stays with its home. Use a registered route and task ID
together, such as `mate/tide-merge`, to refer to a secondmate's task.

The classic front page is used when the conversation has no topics. Adoption
counts status questions tagged with this conversation’s `session_id` in its
Firstmate home. Older metrics without conversation attribution stay out of
the page.

## Links and output

**Links from this session** includes HTTP and HTTPS links and explicit in-home
paths such as `data/launch/preview/index.html` from the owner's messages and Firstmate's
replies to them. Quarterdeck opens eligible reports and pages under `data/<id>/`.
It excludes hidden paths, `config/` and `state/` directories, and filenames
that suggest credentials or secrets. It also omits web links carrying credentials,
tokens or signing parameters. Classic, Lavish and `render --all` include eligible links. JSON exports
and standalone artifacts list the local document names. Exported report text follows the existing
`--bodies` setting.

Re-rendering in the same directory retires obsolete pages verified by
`.quarterdeck-render.json` or a legacy Quarterdeck filename and page marker.
Normal and Lavish renders can coexist while they reflect the same scoped
content. User files
and unverifiable files, including untracked legacy raw documents, are
preserved.
