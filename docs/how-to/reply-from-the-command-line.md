# Reply from the command line

Firstmate answers an ask by adding a reply to the question it came from. The
note for an ask starts with `ASK` and the question ID, such as `ASK Q7.1`.

```sh
quarterdeck reply Q7.1 --text "The preview uses the tide tables from Friday."
```

A reply can carry links and images. Pair each `--link-text` with a `--url`, and
each `--image` with an `--alt`, in the order given.

```sh
quarterdeck reply Q7.1 --text "Here are both layouts." \
  --link-text "Open the preview" --url https://example.com/preview \
  --image ./quay-red.png --alt "The quay in red" \
  --image ./quay-blue.png --alt "The quay in blue"
```

Images follow the same rules as [question images](../reference/item-store.md#question-images):
PNG, JPEG or still WebP, up to 5 MB, copied into the private store now. A reply
holds up to 6 links and 6 images.

Quarterdeck prints `Q7.1 Replied`. The question shows **Replied** on its card
and on its page until the owner opens the question page.

A reply never changes the question's answer or state. Run `quarterdeck show Q7`
to read the whole conversation, with each message's author and UTC time.
See the [CLI reference](../reference/cli-and-config.md#items) for the command.
