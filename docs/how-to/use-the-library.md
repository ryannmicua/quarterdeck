# Find references and answered questions

Select **Open library** in the served Desk header. On a phone it opens a bottom
sheet. On a desktop it opens a drawer on the right. Press Escape or select
**Close library** to return to the Desk.

The pop-out has two tabs.

- **References** lists saved links and lists, newest first.
- **Answered** lists question items that have left the Desk, newest saved items
  first. Select an item title to read its questions and saved answers.

## Search

Use the search box in either tab to find words in the title or description.
**Answered** also searches the question and answer text. Every word must match.
Enter `Q5` to find item Q5, or `Q5.3` to find Q5 if it has a third question.

When there’s enough room, results scroll beneath the search box. In a short
window, such as when the phone keyboard is open, the whole sheet or drawer
scrolls. Scroll back to the top to reach Search, the tabs, or **Close library**.

## When an item moves

An item moves to **Answered** when every question is answered or closed and no
non-closed question has pending delivery.
It returns to the Desk when any question is open again, for example after
`quarterdeck mark Q5.2 open`. Nothing is deleted. Only `quarterdeck remove`
deletes an item.

## Rendered pages

A page written by `quarterdeck render` shows References and Answered as plain
sections after the Desk. The Answered section links each title to its item page.
