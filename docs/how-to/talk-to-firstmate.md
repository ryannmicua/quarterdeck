# Talk to Firstmate

You or Firstmate can start a conversation about any topic. Each conversation
is its own saved item, with the topic as its title. To send a message, use the
served page with [`firstmate_home` configured](read-answers.md#connect-the-inbox)
to point to an existing directory. The Desk shows **Talk to Firstmate** once
that directory is available.

## Start a conversation

1. On the Desk, select **Talk to Firstmate**.
2. Type a short **Topic** and your **Message**, then select **Start conversation**.

Quarterdeck saves the conversation under a new ID such as Q12, attempts delivery
to Firstmate's inbox, and opens the conversation. If the inbox cannot accept
the message, the card opens with the first message marked **Not delivered**.
Conversations leave question answers and states unchanged.

## Send more messages

Type in **Your message** on the conversation and select **Send message**. The
conversation shows messages from oldest to newest. Each of your messages
shows **Sent** or **Not delivered**. Select **Send message again**
under a message that shows **Not delivered** once the
[inbox is available](read-answers.md#connect-the-inbox). The retry keeps the
original message and note IDs. To send or retry in a conversation already
[bound to a Firstmate home](../reference/item-store.md#conversations), point
`firstmate_home` to the bound directory and restore access if needed.

## Read replies

The card labels a standalone message **Firstmate update**. A reply links to
the message it answers. Reload, send a message, or retry delivery to refresh
the conversation. The card acknowledges replies and updates when it displays
them. See
[where unread replies and updates appear](use-the-library.md#when-an-item-moves)
and [how Firstmate replies](reply-from-the-command-line.md#reply-in-a-conversation).

## Find a conversation later

Select **Open library** and choose **Conversations**. See
[how to search and filter the library](use-the-library.md#search) and
[how to set a conversation's project and tags](classify-items.md#change-it-later).
