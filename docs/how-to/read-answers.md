# Read answers

Run `quarterdeck show Q7` to read each question's state, any saved Desk answer
with its UTC timestamp and delivery status, and the latest off-Desk mark.

If a decision came through another channel, mark the open question answered
and use `--via` to record its [source](../reference/cli-and-config.md#classification).
The question page shows the latest off-Desk source and the time it was marked.
Any saved Desk answer stays visible. After an off-Desk mark, a previously
pending Desk answer shows as **Sending stopped**. To send a new Desk decision,
edit the saved answer.
Use the item ID to mark every open question in the item. Both forms report
any answered or closed question they skip.

```sh
quarterdeck mark Q7.3 answered --via chat
quarterdeck mark Q7 closed
quarterdeck mark Q7.3 open
```

Reopen an answered or closed question before recording an off-Desk answer.
See [Read the report](read-the-report.md) for counts that include earlier marks.
If a question is reopened between reading it and marking it by hand, use
`quarterdeck mark Q7.3 open` to restore its Open state.

Marking the last open question answered or closed moves its item from the
Desk to **Answered** in the [library](use-the-library.md). Marking any question
open returns the item to the Desk.

Closed questions show their saved answers but have no editor. Reopen a closed
question before editing its answer. See the [CLI reference](../reference/cli-and-config.md#items)
for the `mark` command.

## Connect the inbox

Set [`firstmate_home`](../reference/cli-and-config.md#json-configuration) in the
Quarterdeck config and restart the serving process.

If delivery fails, check the binding and the home's [inbox command](../explanation/privacy-and-architecture.md#what-quarterdeck-reads-and-writes).
Then [retry the saved answer](answer-questions.md) from the card.
