# Read answers

Run `quarterdeck show Q7` to read each question, its answer, its UTC timestamp and its delivery status. Firstmate receives a note for each submitted answer, beginning with the question ID, such as `Q7.3`.

If a decision came through another channel, mark that question answered. To finish or reopen the whole item, use its item ID.

```sh
quarterdeck mark Q7.3 answered
quarterdeck mark Q7 closed
quarterdeck mark Q7.3 open
```

Marking changes the state locally and preserves the last saved answer. Open the served card to submit a new answer.

## Connect the inbox

Set `firstmate_home` to the absolute path of the intended Firstmate home in the Quarterdeck config. Restart the serving process after changing its config. If exactly one home was registered with an older installation, Quarterdeck uses that home's path by default. With several registered homes, choose one explicitly.

Delivery runs the home's `bin/fm-inbox.sh note` command. If delivery fails, restore the binding or the inbox command and send the saved answer again from the card.
