# Talk to Firstmate

You or Firstmate can start a conversation about anything. Each conversation
is its own saved item, with the conversation title as its name. To send a message, use the
served page with [`firstmate_home` configured](read-answers.md#connect-the-inbox)
to point to an existing directory. The Desk opens on **Conversations**. Its
**New conversation** button sits above the list once that
directory is available. Switching tabs keeps the same page address. See the [glossary](../reference/glossary.md) for the
words the Desk uses.

## Read the Desk like a mailbox

On a screen at least 60rem wide, the Desk has two parts. The tabs and their
lists are on the left. The conversation or question set you select opens on
the right, without leaving the Desk, and its row stays marked in the list.

On a narrower screen the lists fill the screen and a row opens the item's own
page. That page has [Back to and Next unread](go-back-to-the-list.md).

A row has no buttons. It shows the title, the first words of the latest
message, the status, and
the time of the latest activity. The title is bold while a reply is unread.

## Start a conversation

1. On the Desk, stay on **Conversations**.
2. Select **New conversation**. A blank message opens beside the list on a wide screen or in a dedicated compose view on a narrow screen. It is to Firstmate, and the cursor moves to **Conversation title**.
3. Type a short **Conversation title** and your **Message**, then select **Start conversation**.

Select **New conversation** again on a wide screen or **Back to Conversations**
on a narrow screen to close the form. The form keeps any unsent text.

Quarterdeck saves the conversation under a new ID such as Q12 and attempts
delivery to Firstmate's inbox. The new conversation joins the top of **Recent
conversations**. On a wide screen it opens beside the list. On a narrow screen
it takes keyboard focus if **Conversations** is visible.
The result appears above the selected tab's contents. If you switch tabs while
the request is in progress, the selected tab stays open. A refused request
keeps the draft in the form. If Quarterdeck saves the conversation but delivery
fails, it shows **Not delivered** and you can retry from its page.
An unsent draft stays in the fields when you switch to **For review** and back.
Conversations leave question answers and states unchanged.

## Order and pins

**Conversations** has three groups: **Unread**, **Pinned** and **Recent
conversations**. Each group lists its conversations by latest activity. A new conversation, message or
reply brings a conversation to the front of its group, and quiet ones sink.
Each row shows the date and time of that latest activity in your time zone.
Open a conversation and select **Pin** to move it into **Pinned**. Select **Unpin** to return
it to its place by activity. Pinning does not change the messages, the unread
reply or whose turn it is. Each group keeps its own order, so a pinned
conversation stays above every recent one.

Select **Unread only** above the list to hide every conversation with no
unread reply. The filter stays when you open a conversation and go back, and
**Next unread** follows it.

## Resolve a conversation

Open a conversation and select **Resolve** to show that the
conversation is settled. A resolved conversation shows a check with
**Resolved**. Select **Reopen** to clear it. Firstmate can do the same with
`quarterdeck resolve` and `quarterdeck reopen`.

Resolving is only a mark. You and Firstmate can still send messages and
updates, and a new message leaves the conversation resolved. Resolve it again
or reopen it yourself when you want it to change.

## Archive a conversation

Open a conversation and select **Archive** to move it
out of **Pinned** and **Recent conversations** and into the
**Archived** tab. Select **Unarchive** to return it. An archived conversation
keeps its **Resolved** mark, its pin, and its project and topic, and it
returns to **Pinned** if you pinned it. You can still open it and send messages, and Firstmate can still
reply. An unread reply on an archived conversation still shows under
**Unread**. Firstmate can archive with `quarterdeck archive` and
`quarterdeck unarchive`. [Hold](use-the-library.md) leaves out archived conversations.

## Group conversations

A conversation is in at most one [project](../reference/glossary.md#conversations),
and in at most one topic inside that project. You create, rename and assign
both. Conversations you already had stay **Ungrouped** until you move them, and
**All** still lists them.

The **Project** filter sits above the tabs on **Conversations**, **For review**
and **References**. It opens on **All**. Choose a project to see its
conversations, including those filed directly in it. Then choose a **Topic** to
narrow the list. Choose **Ungrouped** to see conversations that are in no
project. The filter also narrows **For review**, and the counts on the tabs
count only the chosen project. A reference or a question set is in no
project until you move it into one. A project then shows it, **Ungrouped**
shows the ones in no project, and **All** shows every one. Archived
conversations stay in **Archived** and leave the filter's lists. Within the
filter, **Unread** comes first, then **Pinned**, and each group keeps its order
by latest activity. The choice stays when you open a conversation and go back,
when you reload the page and when the lists update on their own. This browser
remembers it. **Watch** and **Open Hold** do not change with it.

- To make a project, select **New project**, type its name and select **Save
  name**. With a project chosen, **New topic** adds a topic to it.
- To change a name, choose the project or topic and select **Rename project**
  or **Rename topic**. The conversations stay where they are, and links to
  them keep working.
- To start a conversation inside a project or topic, choose it in the filter
  first. The line under the form says where the conversation will start, such
  as "Starts in Field Atlas / Wayfinding." From **All** or **Ungrouped**, it
  starts ungrouped.
- To change where a conversation, a saved reference or a question set is, open it and select **Move**, choose the
  project and topic, and select **Save move**. The button shows once you have made a project. Choose **Ungrouped** to
  take it out of its project. Moving changes nothing else about the
  conversation: its messages, pin, unread reply and place by latest activity stay.

Firstmate can file a conversation in a project or topic by its ID when it
opens the conversation, and can move one, from the [command line](reply-from-the-command-line.md#file-a-conversation-in-a-project).
It cannot create or rename a project or topic. Being in a project gives a
conversation no access to the others and shares no context with them.

## Send more messages

Type in **Your message** on the conversation and select **Send message**. The
conversation shows messages from oldest to newest, as one stacked thread. The
newest message is open, and so is every reply you have not read and every
message that was not delivered. Each earlier message is folded to one line with
its sender, its first words and its time. Select a line to open it, and select
its top line or its header again to fold it back. A link to the message opens
it again. The message
that awaits your review stays open. Each of your messages
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

A conversation with an unread Firstmate message sits under **Unread** on
**Conversations**, with a bold title. The tab shows how many rows are there.
Opening the row takes you to the unread message and marks that message read.
On a wide screen the row keeps its place under **Unread** until you select
another row, then takes its place by activity. A reply alone does not put a
conversation in **For review**. See [what does](answer-questions.md#what-for-review-holds). A conversation page opened without a link to a
message starts at its first unread message, or at the latest message when all
are read. While the Desk is visible, its lists and counts update on their own
within about ten seconds, and a conversation open beside them loads a new reply
unless you have typed in it. See [how the Desk checks](../reference/cli-and-config.md#conversation-endpoint).
Otherwise, reload, send a message, or retry delivery to
refresh the conversation. Replies shown after a send or retry stay unread
until you open them from the Desk. See [how Firstmate replies](reply-from-the-command-line.md#reply-in-a-conversation).

## Create a task

A conversation can hold work for Firstmate. See [create a task](create-a-task.md).

## Find a conversation later

Every conversation stays in **Conversations** on the Desk. To search them,
select **Open Hold** and choose **Conversations**. See
[how to search and filter Hold](use-the-library.md#search) and
[how to set a conversation's project and tags](classify-items.md#change-it-later).
