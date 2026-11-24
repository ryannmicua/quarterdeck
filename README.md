# ⚓ quarterdeck

### A local lookout for the work Firstmate is waiting to review.

## What it is

Quarterdeck turns the work in your current Firstmate conversation into a local
HTML review page. Serving is read-only by default, with opt-in report review
marks. You can register several Firstmate homes.
See [Configure multiple homes](docs/how-to/configure-multiple-homes.md).

Render on demand, or run `quarterdeck serve` for a page that stays current on
its own. Open the result in a browser, or hand a rendered file to `lavish-axi`
as a local artifact. Quarterdeck itself has no agent or build step.

Quarterdeck can also bring you to the page with an opt-in phone notification
when a new item needs you. Tapping it opens Quarterdeck on that item. See
[Get phone notifications](docs/how-to/serve-the-page.md#get-phone-notifications)
for server requirements, phone setup and withdrawal behavior.

## Features

- Session view: the page shows work from your current conversation with
  Firstmate and collects links under **Links from this session**. See
  [See only this session](docs/how-to/see-only-this-session.md).
- One look on every screen: generated pages and the cloud artifact share one
  stylesheet; the standalone decision brief uses the same visual style.
  Phones get one column; wider screens get columns (from 1024px, Calls beside Sent and the
  ask box; from 1440px, Changed beside Topics,
  a topic's state beside its conversation, a report beside its review buttons,
  search filters as a sidebar) while reading lines stay about 75 characters.
  Heading counts appear only when there is more than one item.
- Needs-you cards and activity: the classic page leads with the decisions,
  merges and other items waiting on you, each a card that
  opens to the ask, options and links and uses the desk's
  [reaction controls and growing reply box](docs/how-to/serve-the-page.md#react-to-calls-and-reports-from-the-page). What
  Firstmate started, merged and finished sits in a collapsed Activity section
  lower on the page. New cards slide in and are marked New until seen, without a
  reload. See
  [Watch Firstmate's activity as cards](docs/how-to/watch-activity-cards.md).
- Captain waits: with `serve --watch-waits` (or `quarterdeck waits watch`), a
  saved wait card shows what kind of ask it is (look at
  a screen, give the word to merge, pick an option, provide a login) with a
  one-line summary, and opens to Firstmate's first briefing and the
  clarifications it gave when you asked, with dates. Cards are released when the
  hold is gone; Discard asks Firstmate to drop a wait. Nothing changes in
  Firstmate. See [Track what is waiting on you](docs/how-to/track-captain-waits.md).
- Topic threads: each topic in the session gets a thread page with what stands
  now, what waits on whom, its
  backlog tasks, the open decisions and the whole conversation, and the front
  page lists the topics. On the page `serve --allow-marks` serves, a decision
  is answered with one tap or in words and five seconds to undo, then folds to
  one line with the answer until Firstmate records it, and an ask box on each
  thread and on the front page sends a question to Firstmate under the topic it
  shows. With topics, the page opens on Calls: one queue of open topic
  decisions, waits on you and items not yet in a topic, each with its options
  as buttons and the recommendation marked.
  An answered call leaves for Sent at once; an unanswered one stays until
  it is answered or its record closes. Use **Finished since you last looked** to catch up on completed work.
  **Changed since you last looked** and the topic list are
  on the Topics tab, everything else is behind search, and the previous front
  page stays at `previous.html` (and the one before at `classic.html`) for one release. `quarterdeck topics check` reports mistakes in the files. See [Keep topic threads](docs/how-to/keep-topic-threads.md) and the
  [topic files reference](docs/reference/topic-files.md).
- Captain board: when a home has `data/captain-board.json`, the desk groups
  decisions still in play, with background items collapsed. Items
  are cross-checked against the live backlog and can carry a decision-brief link
  and recommendation chip. Backlog items missing from the board still appear,
  marked "Not yet sorted". See the
  [board file reference](docs/reference/captain-board.md).
- Board writer: `quarterdeck board upsert|touch|remove <task-id>`,
  `quarterdeck board active --session-id ID` and `quarterdeck board list` let Firstmate keep the board current without
  hand-editing JSON; the writer changes only
  the board file. See [Keep the captain board current](docs/how-to/keep-the-board-current.md).
- Decision briefs: a touch-friendly, theme-aware
  [brief template](templates/decision-brief.html.tmpl) that Firstmate fills and
  publishes as a private Claude artifact. See
  [Write and publish a decision brief](docs/how-to/write-decision-briefs.md).
- Review buttons: start `quarterdeck serve --allow-marks` and use "Mark
  reviewed", "Discard", "Unmark" and "Undiscard" on the served page. They run
  the same code as `quarterdeck reports mark-reviewed`, `unmark-reviewed`,
  `discard` and `undiscard`; off by default. Discard takes a report off the
  list without claiming you reviewed it.
  See the [serving guide](docs/how-to/serve-the-page.md#mark-reports-reviewed-from-the-page).
- Reactions: with `--allow-marks`, the served page offers
  [quick reactions, one-tap answers and replies](docs/how-to/serve-the-page.md#react-to-calls-and-reports-from-the-page)
  delivered to Firstmate. The cloud artifact records reactions and replies for
  `quarterdeck reactions import`, and a "Discuss with Firstmate" button that
  opens the claude.ai comment composer on the card.
- Relevance: Firstmate marks reports and past captain calls current or stale
  (`reports set-relevance`, `calls set-relevance`); stale ones are hidden by
  default behind a collapsed section. A collapsed "Related to this session"
  section below the desk surfaces related work within the current conversation.
  See [Keep the desk relevant](docs/how-to/keep-the-desk-relevant.md).
- Cloud artifact: `quarterdeck export --artifact FILE` builds a Claude
  artifact page from [`templates/artifact.html.tmpl`](templates/artifact.html.tmpl)
  and a data snapshot; marks and discards made there come back with
  `quarterdeck import`. Clicking a card opens a detail view in the page (ask,
  recommendation, detail text, links, linked report text, reaction controls),
  and `--bodies desk` keeps the snapshot small by carrying report text only for
  reports tied to the desk. A status line in the header and at the top of the
  desk says whether reactions are connected. See
  [Use the cloud artifact](docs/how-to/use-the-cloud-artifact.md).
- Compact header on the previous page: a status line says what needs you and
  shows nonzero underway and report totals, each linking to its section.
  Section links form a sticky strip on smaller screens and a sidebar on wide
  screens, followed by the cards that need you.
- Bearings-shaped page: Captain's Call, Underway, and Charted Next, each
  rendered with an empty-state sentence. Charted Next and Details by home start
  collapsed; Underway says how long each task has run. Recent work uses the
  six-hour feed.
- Captain's Call cards include local hold context, options, and recommendations
  when available. Remote decisions show the snapshot summary and point to their
  secondmate home for the full background.
- Decisions and recent work: your priority projects first (set with
  `quarterdeck priorities set`, up to three), the decisions still in play, and
  the last six hours of merged and started work from Firstmate's fleet ledger,
  shown only when there is some. See the guide for background and deferred decisions.
  `quarterdeck attention --json` prints attention data for selected homes, and
  `quarterdeck attention --check` validates Firstmate's formats after an
  update. See [See recent work](docs/how-to/see-recent-work.md).
- Views: a rail of saved briefing views (Morning and Priorities to start; a row
  of buttons on a phone), each an ordered stack of Waiting decisions and needs, recent work,
  priority projects and For your information, with an editor. An item shows
  once, in the first section that claims it. `quarterdeck views` manages them
  and `quarterdeck attention --json --view` prints those sections for selected homes.
- Uses Firstmate's own `bin/fm-bearings-snapshot.sh --json` when a home has it,
  and says so when it falls back to parsing the backlog.
- `quarterdeck serve` serves the page over HTTP, or HTTPS with `--tls-cert`
  and `--tls-key`. `quarterdeck service` can print or write a systemd user
  unit; see the [serving guide](docs/how-to/serve-the-page.md).
- `--base-path /qd` (or `base_path` in config) serves the page under a path
  prefix behind a reverse proxy, such as Caddy, so the address needs no port
  number; see [the proxy example](docs/how-to/serve-the-page.md#serve-it-under-a-path-prefix-behind-a-reverse-proxy).
- [Phone notifications](docs/how-to/serve-the-page.md#get-phone-notifications)
  for new needs-you items, carrying no work content.
- Scout reports with an explicit recommendation when the report states one.
- Stable report IDs, Markdown and readable HTML reading, and local review state (reviewed, discarded, relevance) in a private SQLite database.
- Reports needing review appear independently of backlog attention.
- Underway tasks and pull requests marked ready for review.
- Attention filtering. See [the CLI reference](docs/reference/cli-and-config.md#command) for `--all`.
- Inline-styled review page with readable local report and backlog pages.
- Optional `quarterdeck render --lavish` creates and opens an annotation-ready Lavish review page.
- Lavish request controls queue a report or backlog-item page request for an armed session listener.
- Python 3 standard library only. Phone notifications also need the
  `cryptography` package; without it they are off.
- Generated pages default to the user's state directory outside the repo.

## Quick Start: Make Ready to Sail

Install Quarterdeck and register a Firstmate home:

```sh
curl -fsSL https://raw.githubusercontent.com/ryannmicua/quarterdeck/main/install.sh | sh
quarterdeck add ~/firstmate
```

For a read-first install, download `install.sh`, read it, then run it. Here
`~/firstmate` is a placeholder for the path to your Firstmate home. Run
`quarterdeck help` for command help or `quarterdeck help --json` for the command
index.

Follow [See only this session](docs/how-to/see-only-this-session.md) to record
a conversation. Run `quarterdeck render` and open the printed `index.html`.
By default it lives at `${XDG_STATE_HOME:-~/.local/state}/quarterdeck/index.html`.
Run `quarterdeck render` again whenever you want a fresh page. To update
the installation later, run `quarterdeck update`.

For the first-render walkthrough, installation, update and refresh how-tos, and
command reference, see the [tutorial](docs/tutorials/first-render.md),
[how-to guides](docs/how-to/), and [CLI reference](docs/reference/cli-and-config.md).
Use `quarterdeck reports list` to find a report ID, then run
`quarterdeck reports read <report-id>` to print it and generate its readable
page. See the [report review how-to](docs/how-to/review-reports.md) for review
commands.

## How it relates to Firstmate

Firstmate runs the crew and stores its backlog and scout reports in a home.
Quarterdeck reads those files and creates a convenient review surface alongside
that workflow. Run `quarterdeck render` to regenerate a saved page; see the
[serving guide](docs/how-to/serve-the-page.md#run-it) for live refresh behavior
and optional reactions sent back to Firstmate.

## Privacy model

The page can contain real task names, recommendations, and paths. By default,
Quarterdeck writes it outside the repository under the user's state directory.
Keep custom output paths outside the repository and selected homes, and keep
real configs outside the repository. See
[privacy and architecture](docs/explanation/privacy-and-architecture.md) for
the files Quarterdeck reads, its optional Firstmate snapshot, and the local-only
mode.

The repo ignores common output folders, local config files, and screenshots.
Run `python3 scripts/privacy_guard.py --staged` before committing; it rejects
generated pages, likely real-data files, host-specific paths, private IPs, and
pull-request links or ticket-shaped keys in staged content. Examples and tests
use invented data.

## FAQ

### Does Quarterdeck keep refreshing in the background?

Yes. `quarterdeck serve` refreshes the page while it is open. See the
[serving guide](docs/how-to/serve-the-page.md) for cache timing and systemd
setup. Without `serve`, run `render` whenever a fresh page is useful, or
schedule it; see
[scheduled refresh](docs/how-to/schedule-refresh.md).

### Is it safe to bind `serve` to my network?

The default is 127.0.0.1. A non-loopback `--host` exposes your real work data
to that network with no login. Keep the default unless you control the network.
With `--allow-marks`, anyone who can open the page can mark or unmark reports,
send reactions to Firstmate and edit saved views; there is no key. See the
[serving guide](docs/how-to/serve-the-page.md#mark-reports-reviewed-from-the-page).

### Does it cost tokens?

No. Quarterdeck does not call a model. See
[privacy and architecture](docs/explanation/privacy-and-architecture.md) for
the optional snapshot's data access.

### Does it contact a service?

See [privacy and architecture](docs/explanation/privacy-and-architecture.md)
for Quarterdeck's network behavior, optional snapshot, and the `--no-snapshot`
mode.

### How do I select several homes?

Register each home with `quarterdeck add`, then run `quarterdeck render`. Use
`quarterdeck list` to see entries and `quarterdeck remove` to unregister one.
These commands use `${XDG_CONFIG_HOME:-~/.config}/quarterdeck.json` by default;
pass `--config FILE` to select another config. See the
[multiple homes how-to](docs/how-to/configure-multiple-homes.md).

### What if there is no Firstmate home setting?

Run `quarterdeck add /path/to/firstmate`, pass `--home /path/to/firstmate`, set
`FM_HOME`, or provide a config with `homes`. Quarterdeck stops with a clear
error if none is set.

## Roadmap

Possible future work: a pinned desk artifact for reading off the local
network, a Listen button and tap-to-answer on briefs, an agent-driven marking
path, multi-user review history, Firstmate extension binding, and hosted or
authenticated views; see the [captain desk experience log](docs/explanation/captain-desk-experience-log.md).
These are out of scope today. Apart from the opt-in report review marks, the local
state commands, and the explicit `quarterdeck board` writer, Quarterdeck remains a read-only view of
Firstmate homes.
