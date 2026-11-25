# Classify items

Give an item a project and tags, and give each question a kind, so you can
find them and see which sorts of questions reach you. The allowed words are
listed in [Classification](../reference/cli-and-config.md#classification).

## Classify when you save

```sh
quarterdeck add --kind questions --title "Pop-up layout" \
  --description "Two layouts to choose between." \
  --project harbor --tag ui --tag product-design --body-file questions.json
```

`--project` takes a plain project name or an `owner/name` repository. You can
repeat `--tag` up to three times. In the questions JSON, each question may
carry its own `kind` and `tags`:

```json
[
  {"question": "How should the pop-up show up?", "context": "Centered or docked.",
   "options": ["Centered", "Docked"], "suggestion": "Docked",
   "kind": "pick", "tags": ["ui"]}
]
```

The kinds are `pick` (choose between options), `confirm`
(yes or no on a proposal), `rule` (set a rule others will follow), `wording`
(choose the words), `scope` (what is in or out), `approve` (permission to go
ahead) and `inform` (nothing to decide, only to read).

Use the exact lowercase spelling for each kind and tag. Leave out `kind` to
save an unclassified question. For an unknown word, the error lists the allowed
values.

## Change it later

```sh
quarterdeck classify Q5 --project harbor --tag architecture --tag bug
quarterdeck classify Q5.3 --kind rule --tag business-rule
```

`Q5` sets the project and tags of the item. `Q5.3` sets the kind and tags of one
question. Options you leave out stay as they are. `--tag` replaces all the tags
already there. The question text, options and answers are never touched.

## See it on the page

Projects appear on reference rows and individual Desk cards. When the Desk
groups cards by project, the project appears in the group heading. Tags appear
as labels. In References and Reviewed questions, a project or tag filter
appears when it can tell entries apart, including when some entries have a
project or tag and others have none.

To count questions by kind, tag and project, see [Read the report](read-the-report.md).
