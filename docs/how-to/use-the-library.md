# Find references and answered questions

Select **Open library** in the served Desk header. It opens a bottom sheet
at viewport widths up to `40rem` and a right-side drawer at wider widths.
Select **Close library**, press Escape, or tap outside the library to return
to the Desk.

The pop-out has three tabs.

- **References** lists saved links and lists, newest first, as a compact bookmark list. Each row shows the
  title, any extra links beside it, then the project, saved date and item ID. A resource's title opens its
  main link and its ID opens the item's own page with the description. A list's title opens its page, and
  its ID is plain text. Hover a row to see its description.
- **Answered** lists question items that have left the Desk, newest saved items
  first. Select an item title to read its questions and saved answers.
- **Conversations** lists conversations started by you or Firstmate, newest
  first, each under its topic.

With a tab focused, use the left or right arrow to switch tabs. Home opens
References, and End opens Conversations.

## Search

**References** and **Answered** search titles and descriptions. **Answered**
also searches question and saved answer text. **Conversations** searches
topics and item IDs. Search ignores case and matches every space-separated
term literally. Enter an exact ID on its
own, such as `Q5` to find item Q5 or `Q5.3` to find Q5 if it has a third
question. An ID-shaped topic such as `Q5.3` also matches in **Conversations**.

Use the [project and tag filters](classify-items.md#see-it-on-the-page) when
available to narrow the lists, including on rendered pages.

After opening an item from the library, use your browser’s Back button to
return to the same tab, search and filters with fresh results. A restored
filter remains selected and can be cleared even if no items match it.
Closing the library clears the saved lookup, so reloading the Desk or
returning from an item opened through the Desk table leaves the library closed.

When there’s enough room, results scroll beneath the search box. The whole
sheet or drawer scrolls when the window is short or the controls leave too
little room for results. Scroll back to the top to reach Search, the tabs,
or **Close library**.

## When an item moves

An item with questions stays on the Desk while it has an open question or a
non-closed question with an answer awaiting delivery. Otherwise it appears under
**Answered**. Pending asks and unread replies leave the item in its current
listing. Both locations can show **Replied**. See
[how to read question-card replies](ask-firstmate.md#read-replies).

Standalone conversations are always in **Conversations**. They also appear on
the Desk while they have unread replies or updates from Firstmate. Both
locations show **Replied** until you [read them on the card](talk-to-firstmate.md#read-replies).

Reopen a question with `quarterdeck mark Q5.2 open` to return
its item to the Desk. The item stays saved until you remove it with
`quarterdeck remove`.

## Rendered pages

When an [HTML export](../reference/cli-and-config.md#pages) has saved items, it shows
References and Answered as plain sections after the Desk. The Answered section
links each title to its item page.
