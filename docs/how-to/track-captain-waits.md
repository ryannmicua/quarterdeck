# Track what is waiting on you

Quarterdeck can keep one card for each task Firstmate holds for you. The card
says what kind of ask it is (look at a screen, give the word to merge, pick an
option, provide a login, or something else) and gives a one-line summary.
Open the card to read Firstmate's first briefing on the item and the
clarifications it gave when you asked about it, each with its date.

Nothing changes in Firstmate. Quarterdeck reads what Firstmate already records:
the backlog, the attribution on each held task, and the Claude
Code sessions saved for the Firstmate home.

## What the watcher does

Each pass of the watcher:

1. Reads each home's backlog. A task held for the captain (`hold-kind:
   captain`) has a wait identified by its home, task ID, `Captain hold set:`
   stamp and conversation ID when present. A hold that predates stamps uses
   the task's filed date.
2. Releases cards whose hold is gone. A card moves to done when the hold is
   lifted, the task closes, or the task leaves the backlog, and it keeps its
   record. A home whose backlog cannot be read changes nothing.
3. Re-holds. When the hold stamp or attributed conversation changes, the
   card is waiting again and gets a fresh briefing, kind and summary. Earlier
   briefings remain in the store.
4. Collects the briefing. Ten minutes after the hold (so Firstmate has
   finished explaining it), Quarterdeck takes Firstmate's messages from twenty
   minutes before the hold to ten minutes after, numbers Firstmate's
   paragraphs, and asks a helper command which paragraphs explain the item,
   which kind of ask it is, and for a one-line summary. Quarterdeck copies the
   chosen paragraphs word for word; the helper never writes the card.
5. Adds clarifications. When you write to Firstmate naming a still-waiting
   item and Firstmate answers, the helper picks the paragraphs of that answer
   that are about the item, and they are added to the card. Paragraphs already
   on the card are not added again. Quarterdeck skips:
   - Firstmate's replies to automatic notices (wake-ups, task notifications,
     artifact comments and slash commands), which are recaps;
   - your messages that name no waiting item;
   - unclear references: a pull request or issue number that two waiting
     items share records nothing.

   "Naming" an item means your message contains its task id, its full title,
   or a pull request or issue number (such as `PR 26`, `pr26` or `issue 75`)
   that only that waiting item mentions in its title or hold note. Messages
   sent back to back before Firstmate answers count as one. Which items a
   message names is decided once, when Firstmate's answer settles, and kept;
   a question skipped as unclear stays skipped after the other item is
   released, even when an item is held again with its old stamp.

A failed or timed-out helper run is retried after 1, 5, 15 and then every 60
minutes. Only one helper runs for a wait at a time, even with several watchers.

## Turn it on

Run the watcher alongside the served page:

```sh
quarterdeck serve --allow-marks --watch-waits
```

`--watch-waits` runs a pass at startup and then every 60 seconds. To keep it
under the systemd user unit, add the same flag to `quarterdeck service --write`.
`--allow-marks` adds the Discard button; without it the cards are read-only.

Or run the watcher on its own:

```sh
quarterdeck waits watch            # until Ctrl+C
quarterdeck waits sync             # one pass, for cron or a timer
quarterdeck waits sync --no-helper # update cards without running the helper
```

## Configure it

All settings are optional. Add a `captain_waits` object to the config, and a
`transcripts` path to a home when its sessions are saved somewhere unusual:

```json
{
  "homes": [
    {"label": "Maple Harbor", "path": "~/firstmate-maple", "transcripts": "~/.claude/projects/-home-me-firstmate-maple"}
  ],
  "captain_waits": {
    "helper_command": ["/usr/local/bin/claude", "-p", "--output-format", "text", "--tools", "",
                       "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
                       "--setting-sources", "", "--model", "sonnet"],
    "helper_timeout_seconds": 180,
    "poll_seconds": 60
  }
}
```

- Transcripts default to Claude Code's folder for the home:
  `${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<home path, every character other
  than a letter or digit replaced by ->`. Quarterdeck reads every `*.jsonl`
  file there, so a conversation split across compactions and resumed sessions
  is read as one. Sessions in which you never typed a message, such as
  headless helper runs, are ignored.
- The default helper is a headless Claude Code run with every tool, MCP
  server, slash command and saved setting turned off, so it can only read the
  prompt on its standard input and print an answer. It cannot write files or
  reach GitHub or any other service. Under systemd, give `claude` as an absolute
  path, because a user unit's `PATH` is short.
- A replacement helper reads the prompt on standard input and prints one JSON
  object: `{"kind": "screen" | "merge" | "choice" | "login" | "other",
  "summary": "...", "paragraphs": [numbers]}` for a briefing, or
  `{"paragraphs": [numbers]}` for a clarification. Text around the object is
  ignored. An unknown kind becomes `other`, a summary is cut to 160 characters,
  and out-of-range paragraph numbers are dropped. It runs in the store's
  `work/` folder with the configured timeout.

See the [CLI and configuration reference](../reference/cli-and-config.md#captain-waits)
for every key.

## Read the cards

For a saved wait card to appear, the task must be in the
[current conversation](see-only-this-session.md) and its current hold must
carry that conversation ID. The briefing and clarifications must match both
the hold stamp and conversation ID. Open a card for these messages, oldest
first. "Decisions and recent work" shows the same summary.
Until the briefing is collected, the card shows the hold note and says the
briefing is being collected; a failing helper shows its error and the next try.

From a terminal:

```sh
quarterdeck waits list           # what is waiting now
quarterdeck waits list --all     # also released and discarded cards
quarterdeck waits list --json    # with the briefing and clarifications
```

`quarterdeck attention --json` adds a `wait` member to each waiting item that
has a card; see the [attention JSON reference](../reference/attention-json.md#captain-waits).

## Discard a wait

When a wait no longer matters, use **Discard this wait** on its card (served
with `--allow-marks`) or run:

```sh
quarterdeck waits discard maple-harbor/tide-merge
```

Quarterdeck sends the home's Firstmate a note through its own
`bin/fm-inbox.sh note`, the same channel reactions use, asking it to drop the
hold. Only after the note is delivered is the card hidden, from the cards,
"Decisions and recent work" and Captain's Call. The captain board keeps its own entry
until Firstmate updates it. The card is released for good when the hold
disappears from the backlog. A new hold can appear again when it belongs to
the current conversation.

## Where the cards are kept

Cards live in `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/captain-waits/waits.json`
(mode 0600), or in `captain_waits.store_dir`. Cards are kept per home under
the home's resolved path, so two homes with the same label never see, reuse or
discard each other's cards; the label is only shown. Selecting two such homes
in one command is refused, because their card keys (such as `maple-harbor/tide-merge`) would
collide; give them labels that differ in letters or digits. A store from an earlier
version, kept by label, is dropped once and rebuilt from the backlogs (its
briefings, clarifications and discards are not carried over). The store holds
Firstmate's explanations and your questions that named an item, so Quarterdeck
refuses a store inside its checkout, a selected home, or a home's data
directory. If the store cannot be read, the page shows a "Captain waits
unavailable" warning and `quarterdeck attention --check` reports it. See
[Privacy and architecture](../explanation/privacy-and-architecture.md).
