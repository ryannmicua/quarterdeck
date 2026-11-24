# Save items to Quarterdeck

Firstmate saves an item when the captain asks, such as "send this to
Quarterdeck" or "save this Lavish page to Quarterdeck". Nothing is saved
automatically. `add` and `remove` change Quarterdeck's own
[item store](../reference/item-store.md). `list` and `show` read it.

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

Give one entry per non-blank line. Prefixes such as `-` and `1.` stay part of the entry.

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

`--body-file -` reads standard input. Quarterdeck copies the body when you
save it.

## Find, read and remove items

```sh
quarterdeck list         # saved items, newest first
quarterdeck show Q3      # one item in full, numbered as on the page
quarterdeck remove Q3    # delete Q3 and retire its ID
```

Use the displayed ID, such as `Q3` or `q3`. To correct a saved item, remove it
and add it again. The new item gets a new ID.

## See them

Run `quarterdeck serve` and open the address it prints, or `quarterdeck render`
to write the pages as files. See [Serve the page](serve-the-page.md).
