# Talk to Firstmate

Start a conversation when you want to talk with Firstmate about any topic, not
only a question card. Each conversation is its own saved item with a topic as its
title, so you can find it again in the library. Use the served page with
[`firstmate_home` configured](read-answers.md#connect-the-inbox). Without it, the
Desk has no **Talk to Firstmate** button.

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
original message and note IDs.

If you start a conversation while the inbox is unavailable, Quarterdeck still
saves it and opens it with the first message marked **Not delivered**.

## Read replies

Firstmate replies from the command line, so reload the page to see a new reply.
A conversation with an unread reply shows **Replied** on the Desk and in the
library. Opening the conversation acknowledges its replies. See
[how Firstmate replies](reply-from-the-command-line.md#reply-in-a-conversation).

## Find a conversation later

Select **Open library** and choose **Conversations**. Search matches the topic
and the text of every message, and the project and tag filters work as they do
elsewhere. Firstmate can set a conversation's project and tags with
`quarterdeck classify`. See [Find references and answered questions](use-the-library.md).
