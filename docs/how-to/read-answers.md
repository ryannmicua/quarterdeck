# Read answers

Run `quarterdeck show Q7` to read each question's state and any saved answer,
including its UTC timestamp and delivery status.

If a decision came through another channel, mark that question answered. Use
the item ID to change every question in it at once.

```sh
quarterdeck mark Q7.3 answered
quarterdeck mark Q7 closed
quarterdeck mark Q7.3 open
```

Closed questions show their saved answers but have no editor. Reopen a closed
question before editing its answer. See the [CLI reference](../reference/cli-and-config.md#items)
for the `mark` command.

## Connect the inbox

Set [`firstmate_home`](../reference/cli-and-config.md#json-configuration) in the
Quarterdeck config and restart the serving process.

If delivery fails, check the binding and the home's [inbox command](../explanation/privacy-and-architecture.md#what-quarterdeck-reads-and-writes).
Then [retry the saved answer](answer-questions.md) from the card.
