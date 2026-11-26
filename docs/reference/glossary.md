# Glossary

These are terms Quarterdeck uses on its pages and in the CLI.

## Pages and tabs

| Word | Meaning |
|---|---|
| Desk | The landing page. It has four tabs. |
| Conversations | The Desk tab that opens first. It holds the form to start a conversation, the Project filter and every saved conversation. |
| New conversation | The button that shows the form to start a conversation. The form stays hidden until you select it or the browser restores unsent text, and selecting it again hides the form and keeps unsent text. |
| Needs you | The [Desk tab for items and notices that need attention](../how-to/answer-questions.md). |
| Under way | The [Desk tab for work in progress](../how-to/see-work-under-way.md). It lists one row for each piece of work in the saved workers reading. |
| Watch | A read-only page for what Firstmate has yet to reply to and what its workers are doing. |
| Workers | The [roster page](../how-to/read-workers.md) that reads the workers of the configured Firstmate home and refreshes the reading. |
| Hold | The pop-out with References, Answered and Conversations, opened with **Open Hold**. |
| Hold conversations | The Conversations tab inside Hold, as a Back to link names it. |
| Back to | The link on an item page, followed by the name of the list the page was opened from. With no list it reads Back to Desk. See [Go back to the list](../how-to/go-back-to-the-list.md). |
| Next unread | The link on an item page that opens the next unread item in the same list. |

## Conversations

| Word | Meaning |
|---|---|
| Conversation title | The short name of a conversation, set when you start it. |
| Message | The text you send. |
| Project | A group you create for related conversations. A conversation is in at most one project. |
| Topic | A subject inside one project. A conversation is in at most one topic, and that topic belongs to its project. |
| Ungrouped | A conversation that is in no project. Every conversation starts there unless you start it inside a project or topic. |
| Move | Change which project and topic a conversation is in, or return it to Ungrouped. |
| All | The default Project filter. It shows every conversation, grouped or not. |
| Pinned | The group above Recent conversations for conversations you chose to keep in view. |
| Recent conversations | Every conversation that is not pinned, newest activity first. |
| Pin, Unpin | [Keep a conversation in Pinned, or return it to its place by activity](../how-to/talk-to-firstmate.md#order-and-pins). |
| Resolved | The mark on a conversation that you or Firstmate marked settled. It shows as a check with the word. A message does not clear it. |
| Resolve, Reopen | [Mark a conversation resolved, or clear the mark](../how-to/talk-to-firstmate.md#resolve-a-conversation). |
| Archived | The Desk tab that holds archived conversations. They are out of Pinned, Recent conversations and the Project filter. |
| Archive, Unarchive | [Move a conversation into Archived, or return it](../how-to/talk-to-firstmate.md#archive-a-conversation). |

## Tasks

A [task](../how-to/create-a-task.md) is a conversation that holds work for Firstmate.
Its page shows the task state and the Description, and the Desk shows a Task mark beside its title.

| Word | Meaning |
|---|---|
| Task | A conversation that holds work for Firstmate. The work can end in a pull request, a page, a report, a mockup, an image or anything else. |
| Task mark | The word Task beside a task's title on the Desk. |
| Create a task | The button, on a conversation page and on the Desk, that shows the form for a new task. On a conversation page the new task is related to that conversation. |
| Title | The short name of a task, a field of the task form. |
| Description | What the task should do, a field of the task form. Quarterdeck sends it to Firstmate when the task starts. |
| Start | Save the task and send Firstmate one message, "Task started.", with the Description. On a draft it starts the task. |
| Save draft | Save the task without telling Firstmate. A draft shows the button Start on its page. |
| Related tasks | The list on a conversation of the tasks created from it. |
| Related conversation | The link on a task to the conversation it was created from. |
| Tasks only | The Desk filter, beside Project, that hides conversations that are not tasks. |
| Approve | Resolve a task that is In review. Firstmate's updates on it count as read. |
| Changes needed | Send Firstmate a note on a task that is In review and return the task to Started. Firstmate's updates on it count as read. |
| Note | The field under Changes needed. What it says goes to Firstmate as your message. |
| Send note, Cancel note | Send the note, or close the field and keep the task In review. |
| Result | The link to what a task produced, shown on a task that is In review. |

### Task states

A task has one state. **Resolved** is the existing [Resolved](#conversations) mark and wins over the others.
**In review** here means the result waits for your review. It is a different record from the
[work status](#work-status-words) of the same name.

| State | Meaning |
|---|---|
| Draft | You saved the task and have not started it. Firstmate has heard nothing. |
| Started | You started the task. Firstmate has the Description. |
| In review | Firstmate posted the result and waits for you. The task shows in **Needs you**. |
| Resolved | You approved the task, or you or Firstmate resolved it. |

## Status words

| Word | Meaning |
|---|---|
| Unread reply | Firstmate replied or posted an update that you have not opened. Opening the reply marks that message read. |
| Replied | The last message in a conversation is from Firstmate and you have read it. |
| Messaged | The last message in a conversation is yours and reached the inbox. On Watch, you have an unanswered delivered message in the current turn. |
| Not delivered | A saved message, ask or answer failed to reach the inbox. |
| Asked | On the Desk, Firstmate asked a question that is still open. On Watch, you asked Firstmate on a question set and it has yet to reply. |
| Answered, Closed | The states of a question. See [question states](item-store.md#question-states). |
| Open | A question that has no answer yet. |

## Work status words

These words show on **Under way**. The Workers page uses the same words that
[its statuses](../how-to/read-workers.md#what-the-statuses-mean) define.

| Word | Meaning |
|---|---|
| Building | The work is under way and the reading names no pull request yet. |
| In review | The work is under way and the reading names its pull request. A [task](#task-states) uses the same words for its own state. |
| Ready to merge | The worker reported it is done and the reading names its pull request. |
| Merged today | The home recorded the work as merged on today's date. |
| Working, Question asked, Stuck, Finished, Unknown | The Workers page word, shown when none of the words above fits. |

**Under way** has two groups. **In progress** holds all work that is not merged.
**Merged today** holds the rest and shows no Status column. The columns are
Work (the title of the work), Project, Status and Links. Links holds
**Question**, which opens the question a **Question asked** row waits on,
**Conversation**, which opens the Quarterdeck conversation about the work, and
**Pull request**. **Last checked** is the time of the saved
reading, in your local time zone.

## Catch-up

| Word | Meaning |
| --- | --- |
| Catch-up | Short notices about finished work. Unread notices appear in **Needs you** and can also be reviewed from the CLI. |
| notice | One [Catch-up entry](../how-to/catch-up.md#read-the-notices-on-the-desk). |
| Mark read | Acknowledge one notice. |
| Mark all notices read | Acknowledge the notices [shown on the Desk](../how-to/catch-up.md#read-the-notices-on-the-desk). |
| Unread | A notice waiting for acknowledgment. |
| Read | An acknowledged notice. |
| item | A saved resource, list, questions item, or conversation. |
| question | A numbered part of a questions item that waits for an answer. |

## Time

Dates show as the browser's medium date, for example Nov 20, 2026. A
conversation's activity is the time of its latest message, or the time it was
saved when that is later.
