# Save items to Quarterdeck

Firstmate uses `add` when the captain asks to save a link, list, or set of
questions. `add` and `remove` change Quarterdeck's own
[item store](../reference/item-store.md). `list` and `show` read it.

## Add an item

With `add`, supply a kind, a title that says what it is about, and a one-or-two
sentence description: what it is and why it was saved, so the captain
recognises it later. `add` prints the new ID. Start a conversation from the
[Desk](talk-to-firstmate.md) or the [command line](reply-from-the-command-line.md#post-an-update-or-open-a-conversation).

See [inline web addresses](../reference/item-store.md#inline-web-addresses) for
where pasted URLs become clickable.

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

Give the captain enough context to decide. Each question needs `question` and
`context`, and `options`, a `suggestion`, or both:

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

`--body-file -` reads standard input. Quarterdeck copies the body when you
save it. The questions are numbered on the page, for example Q3.1 and Q3.2.
See [Answer questions](answer-questions.md) to send decisions.

### A question that points to a review page

To send readers to an external review page, add `links` to the question. Use
descriptive link text so the reader knows what they will see. To show local
mockups on the item page, see [Questions with images](#questions-with-images).
For link field requirements, see the
[item store reference](../reference/item-store.md#format-version-1).

```json
[
  {"question": "How should the pop-up show up?",
   "context": "The review page shows the centered and docked layouts.",
   "options": ["Centered", "Docked"], "suggestion": "Docked",
   "links": [{"text": "See the mock-ups", "url": "https://example.org/review/popup"}]}
]
```

A question may also carry a `kind` and `tags`, and the item a project and
tags; see [Classify items](classify-items.md).

The item page shows the links above the question's options and suggested
answer. The links open in a new tab.

### Questions with images

You can attach an overview to the question, a mockup to individual options,
or both. You can mix pictured options with plain text options.

```json
[
  {"question": "Which card style?",
   "context": "Both fit a phone.",
   "images": [{"path": "~/shots/overview.png", "alt": "Both card styles side by side"}],
   "options": [
     {"label": "Plain", "image": {"path": "~/shots/plain.png", "alt": "A card with no frame"}},
     {"label": "Framed", "image": {"path": "~/shots/framed.png", "alt": "A card with a thin frame"}}
   ]}
]
```

Save the JSON with the same `quarterdeck add --kind questions` command as
above. Check [who can view saved images](serve-the-page.md#security-loopback-by-default)
before attaching them.
See the [image requirements and storage rules](../reference/item-store.md#question-images)
and [how to view and pick pictured options](answer-questions.md#view-mockups).

#### Known limits

- On a case-insensitive filesystem, Quarterdeck treats path spellings that
  differ only by case as separate paths.
- Quarterdeck requires a umask that preserves the owner's read, write, and
  search permissions.

## Find, read and remove items

```sh
quarterdeck list         # saved items, newest first
quarterdeck show Q3      # read Q3
quarterdeck remove Q3    # delete Q3 and retire its ID
```

Use the displayed ID, such as `Q3` or `q3`. To correct an item you saved with
`add`, remove it and add it again. The new item gets a new ID.

## See them

Run `quarterdeck serve` and open the address it prints, or `quarterdeck render`
to write the pages as files. See [Serve the page](serve-the-page.md).
