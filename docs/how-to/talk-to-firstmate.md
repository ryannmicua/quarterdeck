# Talk to Firstmate

You or Firstmate can start a conversation about anything. Each conversation
is its own saved item, with the conversation title as its name. To send a message, use the
served page with [`firstmate_home` configured](read-answers.md#connect-the-inbox)
to point to an existing directory. The Desk opens on **Conversations**. Its
**Conversation title** and **Message** fields sit directly above the list once that
directory is available. Switching tabs keeps the same page address. See the [glossary](../reference/glossary.md) for the
words the Desk uses.

## Start a conversation

1. On the Desk, stay on **Conversations**.
2. Select **New conversation**. The cursor moves to **Conversation title**.
3. Type a short **Conversation title** and your **Message**, then select **Start conversation**.

Select **New conversation** again to hide the form. Unsent text stays in the hidden form, and the form closes after Quarterdeck starts the conversation.

Quarterdeck saves the conversation under a new ID such as Q12 and attempts
delivery to Firstmate's inbox. The new conversation joins the top of **Recent
conversations** and takes keyboard focus if **Conversations** is visible.
The result appears above the selected tab's contents. If you switch tabs while
the request is in progress, the selected tab stays open. A refused request
keeps the draft in the form. If Quarterdeck saves the conversation but delivery
fails, it shows **Not delivered** and you can retry from its page.
An unsent draft stays in the fields when you switch to **Needs you** and back.
Conversations leave question answers and states unchanged.

## Order and pins

Each group lists its conversations by latest activity. A new conversation, message or
reply brings a conversation to the front of its group, and quiet ones sink.
Each row shows the date and time of that latest activity in your time zone.
Select **Pin** on a row to move it into **Pinned**. Select **Unpin** to return
it to its place by activity. Pinning does not change the messages, the unread
reply or whose turn it is. Each group keeps its own order, so a pinned
conversation stays above every recent one.

## Resolve a conversation

Select **Resolve** on a row, or on the conversation page, to show that the
conversation is settled. A resolved conversation shows a check with
**Resolved**. Select **Reopen** to clear it. Firstmate can do the same with
`quarterdeck resolve` and `quarterdeck reopen`.

Resolving is only a mark. You and Firstmate can still send messages and
updates, and a new message leaves the conversation resolved. Resolve it again
or reopen it yourself when you want it to change.

## Archive a conversation

Select **Archive** on a row, or on the conversation page, to move a
conversation out of **Pinned** and **Recent conversations** and into the
**Archived** tab. Select **Unarchive** to return it. An archived conversation
keeps its **Resolved** mark, its pin, and its project and topic, and it
returns to **Pinned** if you pinned it. You can still open it and send messages, and Firstmate can still
reply. An unread reply on an archived conversation still shows in
**Needs you**. Firstmate can archive with `quarterdeck archive` and
`quarterdeck unarchive`. [Hold](use-the-library.md) leaves out archived conversations.

## Group conversations

A conversation is in at most one [project](../reference/glossary.md#conversations),
and in at most one topic inside that project. You create, rename and assign
both. Conversations you already had stay **Ungrouped** until you move them, and
**All** still lists them.

The **Project** filter sits between the form and the list. It opens on **All**.
Choose a project to see its conversations, including those filed directly in
it. Then choose a **Topic** to narrow the list. Choose **Ungrouped** to see
conversations that are in no project. Archived conversations stay in
**Archived** and leave the filter's lists. Within the filter, **Pinned** comes first
and each group keeps its order by latest activity. The filter stays when you
open a conversation and go back. **Needs you**, **Watch** and **Open Hold** do
not change with it.

- To make a project, select **New project**, type its name and select **Save
  name**. With a project chosen, **New topic** adds a topic to it.
- To change a name, choose the project or topic and select **Rename project**
  or **Rename topic**. The conversations stay where they are, and links to
  them keep working.
- To start a conversation inside a project or topic, choose it in the filter
  first. The line under the form says where the conversation will start, such
  as "Starts in Field Atlas / Wayfinding." From **All** or **Ungrouped**, it
  starts ungrouped.
- To change where a conversation is, select **Move** on its row, choose the
  project and topic, and select **Save move**. Choose **Ungrouped** to
  take it out of its project. Moving changes nothing else about the
  conversation: its messages, pin, unread reply and place by latest activity stay.

Firstmate can file a conversation in a project or topic by its ID when it
opens the conversation, and can move one, from the [command line](reply-from-the-command-line.md#file-a-conversation-in-a-project).
It cannot create or rename a project or topic. Being in a project gives a
conversation no access to the others and shares no context with them.

## Send more messages

Type in **Your message** on the conversation and select **Send message**. The
conversation shows messages from oldest to newest. Each of your messages
shows **Sent** or **Not delivered**. Select **Send message again**
under a message that shows **Not delivered** once the
[inbox is available](read-answers.md#connect-the-inbox). The retry keeps the
original message and note IDs. To send or retry in a conversation already
[bound to a Firstmate home](../reference/item-store.md#conversations), point
`firstmate_home` to the bound directory and restore access if needed. Restart
the server if you changed the setting.

## Read replies

The card labels a standalone message **Firstmate update**. A reply links to
the message it answers. Attached images fit inside their message. Select an
image to open it at full size in a new tab.

A conversation with an unread Firstmate message shows **Unread reply** on its
row in **Conversations**. See [when it also appears in Needs you](use-the-library.md#when-an-item-moves).
Opening the row takes you to the unread message and marks that message read.
The row stays marked while other unread replies remain. The conversation
keeps its place by activity. A conversation page opened without a link to a
message starts at its first unread message, or at the latest message when all
are read. Reload, send a message, or retry delivery to
refresh the conversation. Replies shown after a send or retry stay unread
until you open them from the Desk. See [how Firstmate replies](reply-from-the-command-line.md#reply-in-a-conversation).

## Find a conversation later

Every conversation stays in **Conversations** on the Desk. To search them,
select **Open Hold** and choose **Conversations**. See
[how to search and filter Hold](use-the-library.md#search) and
[how to set a conversation's project and tags](classify-items.md#change-it-later).
