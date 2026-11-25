# Reply from the command line

Use the target and message IDs from the inbox note. A note that begins
[`ASK`](../reference/cli-and-config.md#ask-endpoint) comes from a question
card. For [`CONVERSATION`](../reference/cli-and-config.md#conversation-endpoint),
see [Reply in a conversation](#reply-in-a-conversation).

For both targets, `quarterdeck reply` must use the live server's
[item store](../reference/item-store.md). If the server uses a custom
`XDG_STATE_HOME`, run the reply command with the same setting.

```sh
quarterdeck reply Q7 --ask "$ask_id" --text "The whole layout is ready for review."
quarterdeck reply Q7.1 --ask "$ask_id" --text "The preview uses the tide tables from Friday."
```

Use the same ask ID to send a follow-up.

To show context on specific cards, reply to the whole set and repeat
`--clarify` once for each affected question. Add `--option-changed` for
a clarified question when an option actually changed. This asks the owner to
review their draft before sending. See [how clarifications appear on cards](ask-firstmate.md#discuss-the-whole-set).

```sh
quarterdeck reply Q7 --ask "$ask_id" --text "The printer sent a new proof." \
  --clarify "Q7.1=The matte cover is unavailable." --option-changed Q7.1 \
  --clarify "Q7.2=The proof uses the same print quantity."
```

A reply can carry links and images.

```sh
quarterdeck reply Q7.1 --ask "$ask_id" --text "Here are both layouts." \
  --link-text "Open the preview" --url https://example.com/preview \
  --image ./quay-red.png --alt "The quay in red" \
  --image ./quay-blue.png --alt "The quay in blue"
```

Reply images use the same [file and alt text requirements](../reference/item-store.md#question-images)
as question images. The live page serves them from the
[private reply image folder](../reference/item-store.md#store-layout).
If a file named `reply-images` occupies that location in the state
directory, move it before attaching reply images.

## Reply in a conversation

Pass the conversation's item ID and use `--ask` for the message ID.

```sh
quarterdeck reply Q12 --ask "$message_id" --text "Use the 2200 K amber."
```

Links, images and follow-ups work as they do for a card ask. Reply to each
message by its own ID and the reply shows in time order. A conversation has no
numbered parts, so `Q12.1` is refused. Run `quarterdeck show Q12` to read the
transcript. See the [CLI reference](../reference/cli-and-config.md#items)
for the transcript fields.

Quarterdeck prints the target and `Replied`, for example `Q7.1 Replied`.
See how to read replies on a [question card](ask-firstmate.md#read-replies)
or [conversation card](talk-to-firstmate.md#read-replies).
See the [CLI reference](../reference/cli-and-config.md#items) for the command.
