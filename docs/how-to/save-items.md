# Save items to Quarterdeck

Firstmate saves an item when the captain asks, such as "send this to
Quarterdeck" or "save this Lavish page to Quarterdeck". Nothing is saved
automatically. Every command below changes only Quarterdeck's own
[item store](../reference/item-store.md).

## Add an item

Every item needs a kind, a title that says what it is about, and a one-or-two
sentence description: what it is and why it was saved, so the captain
recognises it later. `add` prints the new ID.

### A resource (a link)

```sh
quarterdeck add --kind resource --title "Calls redesign" \
  --description "The Lavish page with the new Calls layout, saved to compare the two card styles." \
  --url https://example.com/lavish/calls --link-text "Calls redesign (Lavish)"
```

Only `http://` and `https://` addresses are accepted. Use link text that says
where the link goes.

### A list

Give the entries one per line; a leading `-`, `*` or `1.` is removed. A JSON
array of strings also works.

```sh
printf '%s\n' "Buy rope" "Paint the hull" "Check the sails" | quarterdeck add --kind list \
  --title "Harbor chores" --description "Chores for the weekend, in order." --body-file -
```

### Open questions

Save questions so the captain can decide from the item alone. Each question
needs `question` and `context`, and `options`, a `suggestion`, or both:

```json
[
  {"question": "Merge the tide tables now?",
   "context": "Checks are green and the preview matches the printed tables.",
   "options": ["Merge", "Hold"], "suggestion": "Merge"},
  {"question": "Which colour for the quay?",
   "context": "Red matches the old signs; blue matches the new boats.",
   "options": ["Red", "Blue"]}
]
```

```sh
quarterdeck add --kind questions --title "Tide launch decisions" \
  --description "The two questions holding the tide launch." --body-file questions.json
```

The questions are numbered on the page: Q3.1, Q3.2. The captain can answer
"Q3.1 merge, Q3.2 blue".

### A note

```sh
quarterdeck add --kind note --title "Quay notes" \
  --description "What we agreed about the quay." --body-file notes.txt
```

`--body-file -` reads standard input. The body is copied in now; editing the
file later does not change the item.

## Find, read and change items

```sh
quarterdeck list           # open items, newest first
quarterdeck list --all     # include done items
quarterdeck show Q3        # one item in full, numbered as on the page
quarterdeck edit Q3 --title "Tide launch: two decisions"
quarterdeck edit Q2 --body-file -    # replace a list, questions or note body
```

`edit` changes the title, description, a resource's `--url` and `--link-text`,
or the body. The kind and ID stay. `list --json` and `show --json` print JSON.
IDs are accepted as `Q3`, `q3` or `3`.

## Finish and remove items

```sh
quarterdeck done Q3      # leaves Saved, stays under Done
quarterdeck reopen Q3    # back to Saved
quarterdeck remove Q3    # deleted; Q3 is never used again
```

## See them

Run `quarterdeck serve` and open the address it prints, or `quarterdeck render`
to write the pages as files. See [Serve the page](serve-the-page.md).
