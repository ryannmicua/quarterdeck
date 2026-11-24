# Answer questions

Find the set under **My desk** on the served page and select
**Answer questions**. The highlighted action opens its set at the first open
question. Select **Review answers** if the set has no open questions or if
you're returning to it under **Reviewed questions**. Each question has an ID
you can cite later.

If the question has [review links](save-items.md#a-question-that-points-to-a-review-page),
open them for context before choosing an answer.

1. Pick an option or type in **Your answer**. Select **Use all suggestions** to fill open questions that have a suggestion and have neither a saved answer nor a draft.
2. Review the answers. **Undo changes** reverses one option choice or bulk suggestion action at a time and keeps any drafts you typed afterward.
3. Select **Send answers**. The button counts the answers ready to send. You can send a few now and return for the rest.

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

## Known limits

- If the saved-items file becomes unreadable or the disk fills, Send can report an error after saving the answer. Restore access or free space, reopen the card, and retry any answer marked **Not delivered**.
- If two tabs edit the same answer while delivery is down, a retry can send the newer submission when both texts match. Reload one tab and check the saved text before retrying.
- If someone removes the item while its question page is open, Send reports an error. Copy your draft before reloading. If the decision is still needed, save the questions again and send the answer from the new card.
- The toolbar can show an earlier send result. Check the delivery status beside the question before sending again.
