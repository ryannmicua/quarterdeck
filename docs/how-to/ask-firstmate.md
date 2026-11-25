# Ask Firstmate from a card

Use an ask when you need something explained before you decide. An ask leaves
the question's state and any saved answer intact. Open the served question
page with [`firstmate_home` configured](read-answers.md#connect-the-inbox) to ask
about the whole item or a particular question.

## Discuss the whole set

Use **Discuss this set** when a premise affects several questions. Select it in
the page bar beside **Send answers**. Read replies in **Messages** under
**Discuss this set** above the questions. Opening that conversation acknowledges
only its replies. A set message carries the item ID, such as **Q7**. An answer
or ask on a numbered card carries its full question ID, such as **Q7.1**.
Firstmate saves its reply on the same item.

Under **Queued to send**, each queued answer names its question ID, such as
**Answer · Q7.1**, and shows the text it will send. Answer drafts join the queue
from their cards. To add one set message, write in **Message about this set**
and select **Queue item**. You can edit the message in the same field while
it is queued. **Remove message** takes it out of the queue and keeps the draft.

Select **Send queued items** to send the queued answers, then the set message.
A message-only send leaves every question open. **Remove from send** keeps the
draft on its card and takes it out of the queue; editing the draft puts it back.
You can send more messages before answering anything.

When Firstmate's reply clarifies a question, **Clarified after discussion**
appears on that card. If the reply says an option changed, the card asks you to
review your draft. Both send buttons hold it until you edit it, pick an option,
or select **Keep this draft**. The open page refreshes the set discussion and
its clarifications without replacing your drafts.

When every question is answered, the discussion stays readable. Select **Read
set discussion** under the item in **Answered** in the Library.

## Ask about one question

1. Type or edit a draft by hand in **Your answer or question**. Selecting
   an option or using **Use all suggestions** fills an answer. **Ask
   Firstmate** is available for a new hand-edited draft. Adding only leading
   or trailing whitespace to a filled answer keeps it an answer.
   If the editor's Undo or Redo restores the option or suggestion text,
   Quarterdeck treats it as the filled answer, ignoring surrounding
   whitespace. Retype it to ask Firstmate. **Undo changes** restores
   the previous draft as it was.
2. Select **Ask Firstmate** below that field. The draft moves to **Messages**
   as a pending ask. Use **Send answers** in the page bar when you're ready
   to submit your decision.
3. Read the ask under **Messages**. Once saved, it shows who wrote it, its
   local time, and **Sent** or **Not delivered**.

You can send another ask while waiting for a reply. The card shows every ask
from oldest to newest, with its replies underneath in the order they arrived.

## Read replies

Firstmate's reply appears under its ask and can include text, links, and images.
Sending or retrying a numbered ask refreshes its conversation. If the refresh
finds an unread reply on a folded
answer, the question expands and its **Messages** panel stays open if you
already opened it.

Select **Messages** on the question to acknowledge the replies shown there.
If it is already open, close and reopen it to acknowledge new replies. A
closed question can also show **Replied**. See
[where to find items with unread replies](use-the-library.md#when-an-item-moves).

## Retry a missed delivery

If an ask stays **Pending** after an interruption or shows **Not delivered**,
select **Send ask again** once the
[inbox is available](read-answers.md#connect-the-inbox). The retry keeps the
original ask and note IDs. Reopen a closed question before sending or
retrying an ask. If the configured Firstmate home has changed, Quarterdeck
refuses the retry. A saved ask keeps its original identity and home after
a conflict.
If a new draft blocks **Send ask again** for an interrupted ask, clear the
field before retrying. If the ask never reaches Quarterdeck, reloading the
page clears it and you’ll need to type it again.

A confirmed rejection before saving returns the text to its editable field.
For a set message, select **Queue item** when it is ready to send again.
**Undo changes** can still undo option selections on other questions.
If the inbox becomes unavailable during a retry, the ask keeps its original
ID because the first attempt may have saved it.
If the Firstmate home changed, copy the draft before reloading.
After changing the text of a rejected draft on a numbered question, choose
**Ask Firstmate** to send it again or **Send answers** to submit it as a decision.
Leading and trailing whitespace do not count as a change. The original
rejected text stays out of the answer batch, including when restored by editor
Undo or **Undo changes**.
