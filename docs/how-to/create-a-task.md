# Create a task

A task is a conversation that holds work for Firstmate. You describe what you want,
Firstmate does it, and the result can be anything: a pull request, a page, a report,
a mockup or an image. A task is stored as a conversation, so it keeps its messages,
its project and topic, and its place on the Desk. See the
[glossary](../reference/glossary.md#tasks) for the words.

## Create a task

Creating a task needs the same [prerequisites](talk-to-firstmate.md) as starting a conversation.

1. On the Desk, stay on **Conversations** and select **Create a task**. Or open a conversation and select **Create a task** on its page.
2. Type a **Title** and a **Description** of what you want done.
3. Select **Start** to save the task and tell Firstmate to begin, or **Save draft** to save it without telling Firstmate.

**Start** sends Firstmate one message, "Task started.", in the task's conversation. The inbox note carries the
Description. If delivery fails, the task shows **Not delivered** and you can send the message again from its page.

A task created from a conversation is related to it. The task page links its **Related conversation**, and the
conversation lists its **Related tasks**. The task is filed in the same project and topic as that conversation.
A task created on the Desk is filed where the **Project** filter points.

## Start a draft

A draft shows **Draft** on the Desk and nothing reaches Firstmate. Open it and select **Start** to begin the task.

## Find tasks

Select **Tasks only** beside **Project** on the Desk to hide conversations that are not tasks. The filter
stays when you open a task and go back.

A task shows a **Task** mark and one state: **Draft**, **Started**, **In review** or **Resolved**.
Existing conversations are not tasks until you create one from them.

## Review a task

Firstmate posts the result and, when you should check it first, puts the task In review with
`quarterdeck task review Q12 --text "..." --url https://example.com/result`. The task then shows in **Needs you**
with its result linked and two choices.

- Select **Approve** to resolve the task. Deciding counts the updates Firstmate posted with the result as read.
- Select **Changes needed**, write a **Note** and select **Send note**. Quarterdeck sends the note to Firstmate
  and returns the task to **Started**.

Without a review, Firstmate resolves the task after posting the result, with `quarterdeck resolve`. You can reopen it.
