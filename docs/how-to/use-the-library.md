# Find references and answered questions

Select **Open library** in the page header. On a phone it opens a bottom sheet;
on a desktop it opens a drawer on the right. The same pop-out is on the Desk and
on every item page. Press Escape or select **Close library** to return to where
you were.

The pop-out has two tabs.

- **References** lists saved links and lists, newest first.
- **Answered** lists question items that have left the Desk, newest answer first.

## Search

Type words to search an item's title, description, questions and answers. Every
word must match. Type an ID to jump to an item: `Q5` finds item Q5, and `Q5.3`
finds its third question and shows the saved answer under the item.

On **Answered**, pick an **Answer date** to list only items answered on that
day in your local time. Combine a date with words to narrow the list. Select
**Clear date** to remove it.

Long lists scroll inside the pop-out, and the search stays at the top.

## When an item moves

An item leaves the Desk when every question is answered or closed and every
answer is delivered. It appears under **Answered** with no further action.
It returns to the Desk when any question is open again, for example after
`quarterdeck mark Q5.2 open`. Nothing is deleted. Only `quarterdeck remove`
deletes an item.

## Rendered pages

A page written by `quarterdeck render` has no pop-out. References and Answered
follow the Desk as plain sections, without search.
