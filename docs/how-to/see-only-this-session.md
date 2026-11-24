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
identify one current conversation. A tie between distinct newest records,
a newest record without a conversation ID, or an unreadable board can leave
the selection empty. For export-specific behavior, see the
[CLI reference](../reference/cli-and-config.md#command).

## What appears

- A topic appears when the chat names its title, ID or
  `data/topics/<id>.md`, or when a board item touched in this session names it.
  The topic includes its entries, open decisions, pieces and links.
- A task appears when the chat names its ID, it is a piece of a session topic,
  or a board touch, dispatch, hold or captain action carries the current
  conversation ID. Use `--session-id ID` on `board touch` and
  `board upsert --touch`. Fleet ledger events can carry `session_id` and a
  timed captain hold can carry `(hold-session-id: ID)` in a backlog bullet or
  `Hold session ID` in a backlog table.
- Reports appear when the chat names their directory ID or when an in-scope
  task or topic points to them. A saved report-to-task relation can bring a
  report into scope. A report's modification time alone does not admit it.
- Activity cards, finished work and saved views follow the same membership.
  See [captain wait cards](track-captain-waits.md#read-the-cards) for how saved
  briefings and clarifications are selected.

Conversation evidence stays with its home. Use a registered route and task ID
together, such as `mate/tide-merge`, to refer to a secondmate's task.

The classic front page is used when the conversation has no topics. See
[Adoption](see-your-adoption.md) for status questions tagged in the conversation.

## Links and output

**Links from this session** includes HTTP and HTTPS links and explicit in-home
paths such as `data/launch/preview/index.html` from the owner's messages and Firstmate's
replies to them. A reference links to a Quarterdeck page if one is available.
Other references show the home-relative path as text. A reference alone does
not publish a copy of the file.
It excludes hidden paths, `config/` and `state/` directories, and filenames
that suggest credentials or secrets. It also omits web links carrying credentials,
recognized tokens or signing parameters. Classic, Lavish and `render --all`
include eligible links. JSON exports and standalone artifacts list local
documents by their home-relative labels. See
[Use the cloud artifact](use-the-cloud-artifact.md) for export options.

Re-rendering in the same directory retires obsolete pages verified by
`.quarterdeck-render.json` or a legacy Quarterdeck filename and page marker.
Normal and Lavish renders can coexist while they show the same conversation
and scoped content. User files and unverifiable files, including untracked
legacy raw documents, are preserved.
