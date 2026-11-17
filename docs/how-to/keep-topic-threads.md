# Keep topic threads

A topic thread is one conversation per piece of work: what stands now, what is waiting on whom, the questions
Firstmate needs answered, and everything said about it in chat or on Quarterdeck. Firstmate writes the thread into
a file in its home; Quarterdeck shows it as a page. The file format is in the
[topic files reference](../reference/topic-files.md).

## Use the front page (owner)

When a home keeps topic files, Quarterdeck's front page holds only what needs you, what changed, your topics, the
ask box and search:

- **Needs you:** every topic decision still waiting on your answer, oldest first, with one-tap answers on the served
  page; a decision you answered from Quarterdeck stays as one uncounted line until Firstmate records it; then every `Waiting on: you` line of an open topic, then, under **Not yet in a topic**, every other item that
  needs you (a desk call, a held task, a report to review) whose task is no open topic's piece. Each such row opens
  the item's own page, or its card on the previous page when it has none. Firstmate gathers old items with no topic
  into one **Older items** topic so they can be cleared in one sitting; once an item's task is a topic's piece, it
  shows through that topic instead.
- **Changed since you last looked:** one row per topic with something new from Firstmate or anyone else since you
  last left the page on this device, with "+N more" when there is more than one entry. The first time, it shows the
  last two days. Reloading the page keeps the same starting point.
- **Topics:** every open topic, those needing you first, with its **Now** line. Closed topics are in search.
- **Ask Firstmate**, on the served page, with **Goes to** (see below).
- **Everything else is in Search:** reports, underway, waiting and queued work, and finished work, all searchable,
  with the filters All, Needs you, Underway, Waiting, Queued and Done. A search with no match lists the topics and
  offers to ask Firstmate.

A report counts as waiting for your review for two weeks. After that it stays findable in search (under All) but
no longer counts as awaiting review; nothing is changed in its review record. A recent report also shows under
**To review** on the topic whose **Links** or **Pieces** name it.

The previous page, with its desk, saved views, Activity, reports and details, stays at `classic.html` for this
release; the front page and search link to it, and it links back. A notification for an item that belongs to no
topic opens on its **Not yet in a topic** row; one for an item whose task is a topic's piece opens that topic. With
no topic files, or with `render --all` or `--lavish`, the
front page is the previous page, as before.

## Read a thread (owner)

1. Open a topic from **Needs you**, **Changed since you last looked**, search, or the **Topics** list on the
   previous page.
2. Tap a topic. Its page starts with the state: **Now**, **Waiting on**, **Pieces** (backlog tasks with their state)
   and **Links**.
3. **Open decisions** come next, one per question, each with Firstmate's recommendation and the options.
4. **Conversation** is everything said about the topic, oldest first, grouped by day in your time zone. Your own
   messages carry a rule down their left edge. Each one says whether it was said in chat or on Quarterdeck.

A page written by `quarterdeck render` is read-only: answer a decision in the Firstmate chat. On the page that
`quarterdeck serve --allow-marks` serves, you can answer and ask from the page itself, as below.

## Answer a decision (owner, served page)

1. On an open decision, tap one of its options. The recommended one is filled in. To answer in your own words, or
   when the decision has no options, type under **Answer in words** and tap **Send**.
2. The card says "Sending … in 5 s" with **Undo**. Tap **Undo** to take it back; otherwise it is sent to Firstmate
   when the five seconds run out.
3. The card folds to one line: the question, "Answered:" with your answer, and when you sent it. A long answer is cut
   to about 80 characters; tap the line to read all of it. The decision stops counting as needing you. The line
   stays after a reload and on your other devices, and goes away once Firstmate records your answer in the topic.
4. If the answer could not be sent, the card stays open and says "Not sent" with the reason. Tap an option again,
   or use **Send** under **Answer in words** to resend your typed answer.

**Discuss** is for a decision you want to talk through: it opens the ask box at the bottom of the page with
"About: <the question>" above it, and sends a question, not an answer. Remove that line to ask about the topic in
general.

## Ask Firstmate (owner, served page)

- **On a thread:** type in the box at the bottom and tap **Send**. Your question appears at the end of the
  conversation as waiting, and Firstmate's answer appears there when it writes it. The page reloads about once a
  minute while you are not typing.
- **On the front page:** under the **Ask Firstmate** box, **Goes to** shows the
  topic your question will be filed under: Firstmate's guess from your words, or **New topic**. Tap **Change** to
  pick another topic or **New topic** before you send.
- **By voice:** use the microphone on your phone's keyboard. The words land in the box for you to check; nothing is
  sent until you tap **Send**.
- What you type is kept on this device as a draft for a week. If sending fails, the box says "Not sent, your words
  are kept" and the reason; send again when Firstmate is reachable.

## Get notified (owner, served page)

With [phone notifications](serve-the-page.md#get-phone-notifications) on, an open decision sends one notification
that shows its question only, and tapping it opens the card. A decision you answered from the page is withdrawn.
When Firstmate answers a question you sent from a thread, one notification says "Firstmate answered" and names the
topic.

When a home has no topic files, the front page stays as it was and nothing else changes.

## Start a topic (Firstmate)

1. Pick a topic id: a short name for the work area in lower case with dashes, such as `maple-harbor-catalog`.
   Group related tasks under one topic rather than one topic per task.
2. Create `data/topics/<topic-id>.md` in the home with a `# Title` line, then `Now:`, any `Waiting on:` lines,
   `Pieces:` with the backlog task ids and `Links:`. Copy
   [the example](../reference/examples/maple-harbor-catalog.md) to start.
3. Add the first entries: what the owner asked for and what you did, each as `## <time> | <who> | <where>` with the
   message under it.
4. Check the file:

   ```sh
   quarterdeck topics check data/topics/maple-harbor-catalog.md
   ```

   Fix every error. A warning is worth fixing too, but the page still shows the file.

## Keep it current (Firstmate)

- Append an entry for every message about the topic, the owner's and yours, wherever it was said. Write the time
  in UTC.
- Update `Now:`, `Waiting on:` and `Pieces:` in place when they change.
- Ask the owner something that needs an answer with a decision entry: `Id:`, `Decision:`, and when there are fixed
  answers, `Options:` and `Recommend:`. Write one decision per question.
- When the owner answers, in chat or anywhere else, append their answer as an entry from `you` with `Re:` naming
  the decision's id. That closes it.
- A question or an answer sent from Quarterdeck arrives as an inbox note that names the topic and says what to
  write: the question as an entry from `you` at `quarterdeck` with `Id: ask-…`, answered with `Re: ask-…`; an
  answer as an entry from `you` at `quarterdeck` with `Re:` naming the decision. A question for a **New topic**
  needs a topic first.
- When a decision no longer matters, append a `firstmate` entry with `Re:` naming it and saying why.
- When the work is finished, set `Status: done`.
- Gather open items that belong to no topic into one topic, such as `older-items`, with a decision or a
  `Waiting on: you` line for each, so the owner sees them on the front page and can clear them in one sitting.
- Run `quarterdeck topics check` after editing. With no file named it checks every configured home.

## Fix a mistake the page reports

The bottom of a thread page lists any mistakes in its file, such as "Line 14: an entry heading starts with its
time". Ask Firstmate to run `quarterdeck topics check` and fix the file. See the
[reference's error handling](../reference/topic-files.md#mistakes) for files that cannot produce a readable thread.
