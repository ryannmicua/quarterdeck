# Read answers

Run `quarterdeck show Q7` to read each question's state and any saved answer,
including its UTC timestamp and delivery status.

If a decision came through another channel, mark that question answered and
say where the answer came from with `--via`: `chat`, `review-page`,
`elsewhere` or `superseded` (or your own words in the
[config](../reference/cli-and-config.md#classification)). `--note` adds a
short note. The question shows as Answered, with an "Answered elsewhere" line
and the note. Use the item ID to change every question in it at once; questions
already answered are left alone.

```sh
quarterdeck mark Q7.3 answered --via chat --note "Decided in the planning chat"
quarterdeck mark Q7 closed
quarterdeck mark Q7.3 open
```

A question answered on the Desk is refused: mark it open first if you want to
record a new answer. Answers given on the Desk are recorded as `desk`. To count
both, see [Read the report](read-the-report.md).

Closed questions show their saved answers but have no editor. Reopen a closed
question before editing its answer. See the [CLI reference](../reference/cli-and-config.md#items)
for the `mark` command.

## Connect the inbox

Set [`firstmate_home`](../reference/cli-and-config.md#json-configuration) in the
Quarterdeck config and restart the serving process.

If delivery fails, check the binding and the home's [inbox command](../explanation/privacy-and-architecture.md#what-quarterdeck-reads-and-writes).
Then [retry the saved answer](answer-questions.md) from the card.
