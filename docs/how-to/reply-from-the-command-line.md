# Reply from the command line

Use the target ID and ask ID from the [inbox note](../reference/cli-and-config.md#ask-endpoint)
to reply in the conversation where the ask was sent. Pass the ask ID with
`--ask`. A note that begins `ASK` comes from a question card. A note that begins
`CONVERSATION` comes from a [conversation](talk-to-firstmate.md), and
[replies go to its item ID](#reply-in-a-conversation).

For both targets, `quarterdeck reply` must use the live server's
[item store](../reference/item-store.md). If the server uses a custom
`XDG_STATE_HOME`, run the reply command with the same setting.

```sh
quarterdeck reply Q7 --ask "$ask_id" --text "The whole layout is ready for review."
quarterdeck reply Q7.1 --ask "$ask_id" --text "The preview uses the tide tables from Friday."
```

Use the same ask ID to send a follow-up.

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

A conversation note reads `CONVERSATION Q12 <message id> Topic: <topic>`, then the
message on the following lines. Reply to the item ID with the message ID:

```sh
quarterdeck reply Q12 --ask "$message_id" --text "Use the 2200 K amber."
```

Links, images and follow-ups work as they do for a card ask. Reply to each
message by its own ID and the reply shows in time order. A conversation has no
numbered parts, so `Q12.1` is refused. Run `quarterdeck show Q12` to read the
whole conversation, and `quarterdeck classify Q12 --project NAME --tag TAG` to set its
project and tags.

Quarterdeck prints the target and `Replied`, for example `Q7.1 Replied`.
See [how to read replies on the card](ask-firstmate.md).
See the [CLI reference](../reference/cli-and-config.md#items) for the command.
