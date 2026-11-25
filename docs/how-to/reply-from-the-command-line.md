# Reply from the command line

Firstmate answers an ask by adding a reply to the question it came from. The
note for an ask starts with `ASK`, the question ID, and the ask ID, followed by
the ask text. Pass the ask ID from that note with `--ask`.

```sh
quarterdeck reply Q7.1 --ask "$ask_id" --text "The preview uses the tide tables from Friday."
```

Each reply requires the ID of an existing ask on that question. Use the
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

Quarterdeck prints `Q7.1 Replied`. The question shows **Replied** on its card
and on its page while any reply is unread.

A reply never changes the question's answer or state. Run `quarterdeck show Q7`
to read the whole conversation, with each message's author, UTC time, and
ask ID or reference. It prints each question's stored state and adds a
separate Replied line while that question has an unread reply.
See the [CLI reference](../reference/cli-and-config.md#items) for the command.
