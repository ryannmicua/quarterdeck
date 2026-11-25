# Answer questions

Find the item on your **Desk** on the served page and tap anywhere on its
card, or select **Answer questions**. The highlighted action opens the item at
the first open question. The action reads **Review answers** when no question
is open but an answer is not yet delivered. Each question has an ID you can
cite later.

An item page lists every question in one list, open questions first. A question
that already has an answer collapses to one line with its question and answer
below the open ones. Tap the line to open it, read the context and edit the
answer. The **Send answers** bar stays at the bottom of the screen while you
scroll, so you can compare answers and send them together.

If the question has [review links](save-items.md#a-question-that-points-to-a-review-page),
open them for context before choosing an answer.

1. Pick an option or type in **Your answer**. Select **Use all suggestions** to fill open questions that have a suggestion and have neither a saved answer nor a draft.
2. Review the answers. **Undo changes** reverses one option choice or bulk suggestion action at a time and keeps any drafts you typed afterward.
3. Select **Send answers**. The button counts the answers ready to send. You can send a few now and return for the rest.

Once every question in an item is answered or closed, and every answer is
delivered, the item leaves the Desk. Find it again under **Answered** in the
[library](use-the-library.md). If a question is opened again, for example with
`quarterdeck mark Q7.3 open`, the item returns to the Desk by itself. Nothing is
deleted; only [`quarterdeck remove`](save-items.md) deletes an item.

**Answered** is the question's state. Submitting a new answer sets it, and
[marking a question](read-answers.md) can set it manually. **Sent** means the
inbox command accepted the note. If you see **Not delivered**, your answer
remains saved. Once the [inbox is available](read-answers.md#connect-the-inbox),
send the unchanged answer again to retry with the same note ID. To correct a
sent answer, edit it and send again. Firstmate receives the correction as a
new note.

Edit the answer to submit a new decision on a reopened question. If a question
changed or its item was removed while you were drafting, the page keeps the
draft and continues with the other queued answers. Copy the conflicting draft
before reloading. If a draft is rejected as invalid, edit it before retrying.

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

- If the saved-items file becomes unreadable or the disk fills, Send can report an error after saving the answer. Restore access or free space, reopen the card, and retry any answer marked **Not delivered**.
- If two tabs edit the same answer while delivery is down, a retry can send the newer submission when both texts match. Reload one tab and check the saved text before retrying.
- If someone removes the item while its question page is open, Send reports an error. Copy your draft before reloading. If the decision is still needed, save the questions again and send the answer from the new card.
- The send bar can show an earlier send result. Check the delivery status beside the question before sending again.
