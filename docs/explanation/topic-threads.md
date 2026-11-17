# Topic threads

Quarterdeck is becoming the place where the owner talks to Firstmate about the work, one conversation per topic.
Until now it showed lists: held tasks, review-ready pull requests, reports, activity. The owner still had to go to
the chat to ask anything, and the chat mixes every topic in one stream. A topic thread keeps everything about one
piece of work together: where it stands, what is waiting on whom, the open questions, and what was said.

## What a topic is

A topic is a work area, not one backlog task. The backlog often holds dozens of open tasks. One thread per task
would bring back the long list the owner stopped reading (see the
[captain desk experience log](captain-desk-experience-log.md)). So one topic groups the tasks of one piece of work,
such as a launch, and lists them under **Pieces**.

Nothing in the backlog says which tasks belong together, and guessing from chat is unreliable. Firstmate decides
which tasks belong to a topic and records the grouping in the topic file.

## Why Firstmate writes a file and Quarterdeck only reads it

Firstmate already holds the conversation, but it replies only in chat. Quarterdeck cannot show a reply it never
sees, and reading chat transcripts to guess the topic of each message is not reliable. So Firstmate writes each
message about a topic, chat or not, to `data/topics/<topic-id>.md`, and Quarterdeck reads that file.

This keeps Quarterdeck's boundary: it reads a home's files and writes none of them. The format is plain Markdown so
that Firstmate can write it by hand with no tool, and tolerant so that one mistake hides one line, not the topic.
`quarterdeck topics check` exists because a hand-written file will have mistakes, and the first mate needs to find
them before the owner does.

## Decisions as fields, not prose

Quarterdeck already turns a call's text into one-tap answers by parsing phrases such as "reply merge or hold". That
works often but not always. In a topic file Firstmate writes the question, the options and the recommendation as
separate fields, so nothing has to be parsed. When one piece of work needs several answers, Firstmate writes one
decision per question; a card that holds seven questions cannot be answered with one tap.

A decision stays open until a later entry answers it with `Re:`. The file stays append-only: an answer never edits
the question.

## The choices this follows

The owner reviewed a clickable mock-up and left six design questions to be picked for them:

1. One thread is a work area that groups several tasks.
2. A phone notification shows the decision's title only: no recommendation or work detail on the lock screen.
3. Dictated words land in the ask box to check before sending; nothing sends by itself.
4. A decision with several questions inside is shown as one card per question.
5. A question asked from the front page shows which topic it will go to before it is sent, and the topic can be
   changed.
6. Saved views and the Activity section give way to search with filters.

## The look

The thread pages use a new visual system, separate from the existing page. The owner found the mock-up's style
generic, so these pages avoid stacks of rounded cards, pills and chips. The conversation reads like a log:
hairline rules, a serif for what people wrote and a small sans-serif for labels and times. One signal colour marks
only what waits on the owner: an open decision and a wait on **You**. The text is large for reading on a phone, and
the pages follow the system's light or dark setting.

## What comes next

This first step ships the file format, the checker and read-only thread pages. Later steps add, in order:

- an ask box on each thread and on the front page, sending to Firstmate through the same `bin/fm-inbox.sh note`
  path reactions use, with the topic attached;
- one-tap answers on decision cards, with the same five-second Undo report marks use, and **Discuss**;
- phone notifications that name the decision and open on its card, and a notification when a question is answered;
- a front page of only **Needs you** and **Changed since you last looked**, with everything else behind search.

Each step keeps the old page reachable until the new one covers what it showed.
