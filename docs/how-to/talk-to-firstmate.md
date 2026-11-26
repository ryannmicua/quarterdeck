# Talk to Firstmate

You or Firstmate can start a conversation about any topic. Each conversation
is its own saved item, with the topic as its title. To send a message, use the
served page with [`firstmate_home` configured](read-answers.md#connect-the-inbox)
to point to an existing directory. The Desk opens on **Conversations**. Its
**Topic** and **Message** fields sit directly above the list once that
directory is available. See the [glossary](../reference/glossary.md) for the
words the Desk uses.

## Start a conversation

1. On the Desk, stay on **Conversations**.
2. Type a short **Topic** and your **Message**, then select **Start conversation**.

Quarterdeck saves the conversation under a new ID such as Q12 and attempts
delivery to Firstmate's inbox. The new topic joins the top of **Recent
conversations** and takes keyboard focus if **Conversations** is visible.
The result appears above the selected tab's contents. If you switch tabs while
the request is in progress, the selected tab stays open. A refused request
keeps the draft in the form. If Quarterdeck saves the topic but delivery
fails, the topic shows **Not delivered** and you can retry from its page.
An unsent draft stays in the fields when you switch to **Needs you** and back.
Conversations leave question answers and states unchanged.

## Order and pins

Each group lists its conversations by latest activity. A new topic, message or
reply brings a conversation to the front of its group, and quiet ones sink.
Select **Pin** on a row to move it into **Pinned**. Select **Unpin** to return
it to its place by activity. Pinning does not change the messages, the unread
reply or whose turn it is. Each group keeps its own order, so a pinned
conversation stays above every recent one.

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
keeps its place by activity. Reload, send a message, or retry delivery to
refresh the conversation. A send or retry acknowledges replies newly shown
by that refresh. See [how Firstmate replies](reply-from-the-command-line.md#reply-in-a-conversation).

## Find a conversation later

Every conversation stays in **Conversations** on the Desk. To search them,
select **Open Hold** and choose **Conversations**. See
[how to search and filter Hold](use-the-library.md#search) and
[how to set a conversation's project and tags](classify-items.md#change-it-later).
