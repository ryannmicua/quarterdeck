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
it is queued. Clear the field to leave the message out of the send.

Select **Send queued items** to send the queued answers, then the set message.
Use **Send answers** when you want to send just the queued answers.
A message-only send leaves each question in its current state. **Remove from
send** keeps the draft on its card and leaves it out of both send buttons.
Change the answer text or select an option on its card to put it back in the
queue. Adding or removing surrounding whitespace keeps it out of the send.
**Use all suggestions** skips removed drafts, including ones you cleared.
You can send more messages before answering anything.

When delivery is incomplete, the send bar counts sent items, items saved
for retry, failed requests, and interrupted sends whose outcome is
unconfirmed. It also counts any answers it skipped after an interruption.

When Firstmate's reply clarifies a question, **Clarified after discussion**
appears on that card. If the reply says an option changed, the card asks you to
review your draft. Make any changes and select **Keep this draft** to release
the hold for both send buttons. A newer option change asks for confirmation
again. The visible page refreshes the set discussion and its clarifications
without replacing your drafts. If **Messages** is already open when a new
reply appears, close and reopen it to acknowledge the reply.

After the item moves to **Answered** in the Library, select its title and
open **Messages** under **Discuss this set** to return to the thread.

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
Reload the page to see new replies to numbered asks. Sending or retrying a
numbered ask also refreshes its conversation. If that refresh finds an unread
reply on a folded answer, the question expands and its **Messages** panel stays
open if you already opened it.

Open a row marked **Unread reply** to go to that message and mark it read.
Select **Messages** on the question to acknowledge the replies shown there.
If it is already open, close and reopen it to acknowledge the replies it shows. A
closed question can also show **Unread reply**. See
[where to find items with unread replies](use-the-library.md#when-an-item-moves).

## Retry a missed delivery

If an ask stays **Pending** after an interruption or shows **Not delivered**,
select **Send ask again** once the
[inbox is available](read-answers.md#connect-the-inbox). The retry keeps the
original ask and note IDs. Reopen a closed question before sending or
retrying its numbered ask. When every question is closed, reopen any question
and reload the page to send or retry a set message. If the configured Firstmate
home has changed, Quarterdeck refuses the retry. A saved ask keeps its original
identity and home after a conflict.
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
