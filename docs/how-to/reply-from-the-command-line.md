# Reply from the command line

Use the target and message IDs from the inbox note. A note that begins
[`ASK`](../reference/cli-and-config.md#ask-endpoint) comes from a set discussion
or a numbered question. For [`CONVERSATION`](../reference/cli-and-config.md#conversation-endpoint),
see [Reply in a conversation](#reply-in-a-conversation).

Run `quarterdeck reply`, `post`, and `converse` with the live server's
[item store](../reference/item-store.md). If the server uses a custom
`XDG_STATE_HOME`, use the same setting for these commands.

```sh
quarterdeck reply Q7 --ask "$ask_id" --text "The whole layout is ready for review."
quarterdeck reply Q7.1 --ask "$ask_id" --text "The preview uses the tide tables from Friday."
```

Use the same ask ID to send a follow-up.

To show context on specific cards, reply to the whole set and repeat
`--clarify` once for each affected question. Add `--option-changed` for
a clarified question when an option actually changed. See
[how clarifications and option changes appear on cards](ask-firstmate.md#discuss-the-whole-set).

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

## Post an update or open a conversation

Before using either command, [update Quarterdeck and restart the live server](update.md)
so it can read standalone updates in the item store.

For `converse`, use the same configuration as `serve`. Pass the live config
with `--config FILE`, or match the server's `XDG_CONFIG_HOME` when it uses the
default config lookup. See the [config reference](../reference/cli-and-config.md#json-configuration)
for the restart requirement after a `firstmate_home` change.

Use `post` to add an update to an existing conversation or `converse` to open
one with a title and a first message from Firstmate.

```sh
quarterdeck post Q12 --text "The amber lamps are installed."
quarterdeck converse --title "Pier lamp install" --text "Started the install."
```

See the [CLI reference](../reference/cli-and-config.md#items) for attachments
and command output. The owner can [respond on the conversation page](talk-to-firstmate.md#send-more-messages).
Use [reply](#reply-in-a-conversation) to answer their message.

## File a conversation in a project

The owner creates and renames [projects and topics](talk-to-firstmate.md#group-conversations)
on the Desk. Firstmate can read them and file a conversation in one. List them
first, then name one by its name or its ID.

```sh
quarterdeck projects
quarterdeck converse --title "Pier lamp install" --text "Started the install." \
  --project "Field Atlas" --topic Wayfinding
quarterdeck move Q12 --project P1 --topic T2
quarterdeck move Q12 --ungrouped
```

A name matches without regard to case. An unknown project or topic is refused
with the names that exist, and nothing is saved. Moving a conversation changes
nothing but where it is filed. Suggest a new project or topic to the owner in
the conversation instead of creating one.
