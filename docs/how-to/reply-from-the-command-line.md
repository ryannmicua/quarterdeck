# Reply from the command line

Firstmate answers an ask in the conversation it came from. The note starts
with `ASK`, the target ID, and the ask ID, followed by the ask text. Use the
question item's ID for a whole-card ask or the numbered ID for an ask about
one question.
Pass the ask ID from that note with `--ask`.

```sh
quarterdeck reply Q7 --ask "$ask_id" --text "The whole layout is ready for review."
quarterdeck reply Q7.1 --ask "$ask_id" --text "The preview uses the tide tables from Friday."
```

Each reply requires the ID of an existing ask in that conversation. Use the
same ID to send a follow-up to that ask.

A reply can carry links and images. Pair each `--link-text` with a `--url`, and
each `--image` with an `--alt`, in the order given.

```sh
quarterdeck reply Q7.1 --ask "$ask_id" --text "Here are both layouts." \
  --link-text "Open the preview" --url https://example.com/preview \
  --image ./quay-red.png --alt "The quay in red" \
  --image ./quay-blue.png --alt "The quay in blue"
```

Images follow the same rules as [question images](../reference/item-store.md#question-images):
PNG, JPEG or still WebP, up to 5 MB per image. The live page serves them from
the [private reply image folder](../reference/item-store.md#store-layout).

Quarterdeck prints the target and `Replied`, for example `Q7.1 Replied`.
The live page shows **Replied** while a reply is unread, and the item's
listing shows the badge as well.

A reply preserves answers and question states. Open **Messages** in
**Item conversation** or on the numbered question to read the reply.
See the [CLI reference](../reference/cli-and-config.md#items) for the command.
