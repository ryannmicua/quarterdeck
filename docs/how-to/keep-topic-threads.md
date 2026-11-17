# Keep topic threads

A topic thread is one conversation per piece of work: what stands now, what is waiting on whom, the questions
Firstmate needs answered, and everything said about it in chat or on Quarterdeck. Firstmate writes the thread into
a file in its home; Quarterdeck shows it as a page. The file format is in the
[topic files reference](../reference/topic-files.md).

## Read a thread (owner)

1. Open Quarterdeck. When the home keeps topic files, a **Topics** section sits under **Needs you**. Each row shows
   the topic, its **Now** line, how many decisions are open and when it was last written to. Topics with open
   decisions come first.
2. Tap a topic. Its page starts with the state: **Now**, **Waiting on**, **Pieces** (backlog tasks with their state)
   and **Links**.
3. **Open decisions** come next, one per question, each with Firstmate's recommendation and the options.
4. **Conversation** is everything said about the topic, oldest first, grouped by day in your time zone. Your own
   messages carry a rule down their left edge. Each one says whether it was said in chat or on Quarterdeck.

The thread pages are read-only for now. Answer a decision in the Firstmate chat; Firstmate records the answer in
the file, and the decision leaves **Open decisions** on the next refresh.

When a home has no topic files, the front page has no Topics section and nothing else changes.

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
- When a decision no longer matters, append a `firstmate` entry with `Re:` naming it and saying why.
- When the work is finished, set `Status: done`.
- Run `quarterdeck topics check` after editing. With no file named it checks every configured home.

## Fix a mistake the page reports

The bottom of a thread page lists any mistakes in its file, such as "Line 14: an entry heading starts with its
time". Quarterdeck shows the rest of the file, so a mistake may hide one entry or field, never the whole topic. Ask
Firstmate to run `quarterdeck topics check` and fix the file.
