# Answer questions

Select **For review** on the Desk. The tab shows the number of items that wait
for you. Each question set counts once, regardless of how many questions are
open. Items you put under **Later** are not counted.

## What For review holds

**For review** holds only what waits for you:

- a question set with an open question, an answer that was not delivered, or a reply you have not read
- a task that waits for [your review](create-a-task.md#review-a-task)
- a conversation in which Firstmate [marked a message for your review](reply-from-the-command-line.md#mark-a-message-for-review)
- a conversation with a message of yours that was not delivered

A conversation with an unread reply and nothing else stays on
**Conversations**, under **Unread**. So do [Catch-up notices](catch-up.md#read-the-notices-on-the-desk).

Each row links the saved ID and title and shows the first open question or
the latest message from Firstmate. Under it are the status, the project and
the date. Select the row to open the first open question. If there is an
unread reply, the row [opens that message](ask-firstmate.md#read-replies). See
[where the page opens](ask-firstmate.md#read-replies). On a wide screen the
item opens beside the list.
The item page has the question wording, options, and suggestions. Each
question has an ID you can cite later.

A question set shows how many questions are open, such as **1 of 2 open**. With
none open and an answer still to deliver, it shows **Answered**. A conversation
shows **Asked**, and a task **In review**. **Unread reply** comes first when there
is an unread reply. The row also shows **Not delivered** while an
answer or retryable ask needs delivery.

## Deal with an item later

Select **Deal with later** on an item that waits for you to put it under
**Later**, at the end of **For review**. The button sits beside **Next unread**,
at the top and at the bottom of the item.
The next item in the list opens, when there is one.

An item under **Later** stays there until you answer it, send a message in it
or resolve it. It leaves the tab's count, and **Next unread** passes over it.
A new reply from Firstmate lifts it back among the waiting items until you
read it. Select **Remove from Later** to take it out yourself.

## Answer on the item page

An item page lists every question in one list. A question marked **Answered**
with a **Sent** answer and no unread replies collapses to one line below the
expanded questions when you open the page.
The line shows the saved answer, followed by **(Desk)** if the question also
has an off-Desk mark. Open the row to see the question and any recorded
answer source. On the served page you can edit the answer there. A saved
correction awaiting delivery returns to the expanded questions. The
**Send answers** bar stays at the bottom of the screen while you scroll,
so you can compare answers and send them together.

After you answer, **Next unread** opens the next item that waits for you.
On a narrow screen **Back to For review** returns you to the list. See [Go back to the list](go-back-to-the-list.md).

If the question has [review links](save-items.md#a-question-that-points-to-a-review-page),
open them for context before choosing an answer.

1. Pick an option or type in the answer field. Select **Use all suggestions** to fill open questions that have a suggestion and have neither a saved answer nor a draft.
2. Review the answers. **Undo changes** reverses one option choice or bulk suggestion action at a time and keeps any drafts you typed afterward.
3. Select **Send answers**. The button counts the answers ready to send. You can send a few now and return for the rest.

When you need more information before deciding, see how to
[ask Firstmate](ask-firstmate.md), or
[send answers together with a set message](ask-firstmate.md#discuss-the-whole-set).

To find an item after it leaves the Desk, open
[**Answered** in Hold](use-the-library.md#when-an-item-moves).

**Answered** is the question's state. Submitting a new answer sets it, and
[marking a question](read-answers.md) can set it manually. **Sent** means the
inbox command accepted the note. If you see **Not delivered**, your answer
remains saved. Once the [inbox is available](read-answers.md#connect-the-inbox),
send the unchanged answer again to retry with the same note ID. To correct a
sent answer, edit it and send again. Firstmate receives the correction as a
new note. Open **Earlier answers** on the served question card to see the
previous answer text and submission times, newest first.

Edit the answer to submit a new decision on a reopened question. If a question
was closed or its saved record changed while you were drafting, the page keeps
the draft and continues with the other queued answers. Copy the conflicting
draft before reloading. For an option change
from Firstmate, [review the draft on the card](ask-firstmate.md#discuss-the-whole-set).
If a draft is rejected as invalid, edit it before retrying.

The page shows answer times in your local time zone. If you're viewing a
[rendered snapshot](../reference/cli-and-config.md#pages), open the served page
before sending an answer.

## View mockups

Open the item page to see mockups below the question text or above their
option labels. On an editable question, tap the mockup or radio button to
pick the option as your draft answer. Inline links in option labels open
their destinations in a new tab. Select **Open image** to see the full
image in a new tab. Tap the image itself to open it full size when it belongs
to the whole question or appears on a read-only option.

## Known limits

- If the saved-items file becomes unreadable or the disk fills, Send can report an error after saving the answer. Restore access or free space, reopen the item page, and retry any answer marked **Not delivered**.
- If two tabs edit the same answer while delivery is down, a retry can send the newer submission when both texts match. Reload one tab and check the saved text before retrying.
- If someone removes the item while its question page is open, Send reports an error. Copy your draft before reloading. If the decision is still needed, save the questions again and send the answer from the new item page.
- The send bar can show an earlier send result. Check the delivery status beside the question before sending again.
