# Talk to Firstmate

Start a conversation when you want to talk with Firstmate about any topic, not
only a question card. Each conversation is its own saved item with a topic as its
title, so you can find it again in the library. Use the served page with
[`firstmate_home` configured](read-answers.md#connect-the-inbox) to point to an
existing directory. The Desk shows **Talk to Firstmate** once that directory
is available.

## Start a conversation

1. On the Desk, select **Talk to Firstmate**.
2. Type a short **Topic** and your **Message**, then select **Start conversation**.

Quarterdeck saves the conversation under a new ID such as Q12, sends your
message to Firstmate's inbox, and opens the conversation. A conversation never
counts as an answer to any question.

## Send more messages

Type in **Your message** on the conversation and select **Send message**. The
conversation shows every message and reply from oldest to newest. Each of your
messages shows **Sent** or **Not delivered**. Select **Send message again**
under a message that shows **Not delivered** once the
[inbox is available](read-answers.md#connect-the-inbox). The retry keeps the
original message and note IDs. The conversation stays with the Firstmate home
where it started. Restore that home in `firstmate_home` to send more messages
or retry delivery.

If the Firstmate home directory is available but its inbox cannot accept
the message, Quarterdeck saves the conversation and opens it with the first
message marked **Not delivered**.

## Read replies

Firstmate replies from the command line. Reload, send a message, or retry
delivery to refresh the conversation.
A conversation with an unread reply shows **Replied** on the Desk and in the
library. The conversation acknowledges replies as they appear on its card. See
[how Firstmate replies](reply-from-the-command-line.md#reply-in-a-conversation).

## Find a conversation later

Select **Open library** and choose **Conversations**. Search matches the topic
or the item ID. The project and tag filters work as they do elsewhere.
Firstmate can set a conversation's project and tags with
`quarterdeck classify`. See [Find references and answered questions](use-the-library.md).
Conversations are available through the served site and excluded from HTML exports.
