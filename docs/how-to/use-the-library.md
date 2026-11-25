# Find references and answered questions

Select **Open library** in the served Desk header. It opens a bottom sheet
at viewport widths up to `40rem` and a right-side drawer at wider widths.
Select **Close library**, press Escape, or tap outside the library to return
to the Desk.

The pop-out has two tabs.

- **References** lists saved links and lists, newest first.
- **Answered** lists question items that have left the Desk, newest saved items
  first. Select an item title to read its questions and saved answers.

With a tab focused, use the left or right arrow to switch tabs. Home opens
References, and End opens Answered.

## Search

Use the search box in either tab to match text in the title or description.
**Answered** also searches question and saved answer text. Search ignores case
and matches every space-separated term literally. Enter an exact ID on its
own, such as `Q5` to find item Q5 or `Q5.3` to find Q5 if it has a third
question.

Use the [project and tag filters](classify-items.md#see-it-on-the-page) when
available to narrow either list, including on rendered pages.

After opening an item from the library, use your browser’s Back button to
return to the same tab, search and filters with fresh results. A restored
filter remains selected and can be cleared even if no items match it.
Closing the library clears the saved lookup, so reloading the Desk or
returning from a Desk card leaves the library closed.

When there’s enough room, results scroll beneath the search box. The whole
sheet or drawer scrolls when the window is short or the controls leave too
little room for results. Scroll back to the top to reach Search, the tabs,
or **Close library**.

## When an item moves

An item stays on the Desk while it has an open question or a non-closed
question with an answer awaiting delivery. Otherwise it appears under
**Answered**. Pending asks and unread replies leave the item in its current
listing. Both the Desk and the library’s **Answered** tab show **Replied** while
an item has unread replies. See [how to read and acknowledge replies](ask-firstmate.md#read-replies).

Reopen a question with `quarterdeck mark Q5.2 open` to return
its item to the Desk. The item stays saved until you remove it with
`quarterdeck remove`.

## Rendered pages

If the store has saved items, a page written by `quarterdeck render` shows
References and Answered as plain sections after the Desk. The Answered section
links each title to its item page.
