# Read answers

Run `quarterdeck show Q7` to read each question's state and any saved answer,
including its UTC timestamp and delivery status.

If a decision came through another channel, mark the open question answered
and use `--via` to record its source. Choose `chat`, `review-page`, `elsewhere`
or `superseded`, using the exact lowercase spelling. The question shows the
source and the time it was marked. Any saved Desk answer and its delivery
status stay on the question.
Use the item ID to mark every open question in the item. Both forms report
any answered or closed question they skip.

```sh
quarterdeck mark Q7.3 answered --via chat
quarterdeck mark Q7 closed
quarterdeck mark Q7.3 open
```

Reopen a question before recording another off-Desk answer. Each mark retains
its own source and time. Answers given on the Desk are recorded as `desk`.
See [Read the report](read-the-report.md) for counts by source.

Closed questions show their saved answers but have no editor. Reopen a closed
question before editing its answer. See the [CLI reference](../reference/cli-and-config.md#items)
for the `mark` command.

## Connect the inbox

Set [`firstmate_home`](../reference/cli-and-config.md#json-configuration) in the
Quarterdeck config and restart the serving process.

If delivery fails, check the binding and the home's [inbox command](../explanation/privacy-and-architecture.md#what-quarterdeck-reads-and-writes).
Then [retry the saved answer](answer-questions.md) from the card.
