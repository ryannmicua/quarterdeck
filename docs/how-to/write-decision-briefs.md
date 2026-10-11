# Write and publish a decision brief

A decision brief is a one-page answer to "what do you want from me, and what do
you recommend?" for one merge, approve, or decide ask. Quarterdeck does not
render briefs. Firstmate fills the template in this repository, publishes the
result as a private Claude artifact, and links it from the board item. The
board then shows the recommendation chip and an **Open brief** button.

The template is [`templates/decision-brief.html.tmpl`](../../templates/decision-brief.html.tmpl).
It is plain HTML and CSS with no scripts or external requests. It follows the
system light or dark setting, uses one column below 700px, and keeps
link tap targets at least 44px wide and tall.

## Sections

Each section answers one of the captain's questions, in reading order. Delete a
section only where the template marks it optional.

| # | Section | Holds | Answers |
| --- | --- | --- | --- |
| 0 | Header | Eyebrow (`Decision brief · <project> · <item>`), a short title the captain can say aloud (the same as the board `title`), status chips for checks, size and caveats. | What is this? |
| 1 | Decision card | The question in business terms, two lines of context, and a recommendation box: a verb (Merge, Approve, Choose B, Wait), a one-line reason, and a reversibility badge (two-way door: cheap to undo; one-way door: hard to undo). | What do you want, and what do you recommend? |
| 2 | How it works | One flow of at most six boxes with a legend. | How does it connect? |
| 3 | Ship vs. wait | Four cells: outcomes it enables and risks it closes, what changes, **Cost of delay**, and **Exposure**. | What do I get, what changes, and what keeps hurting if I wait? |
| 4 | Options (decide only) | Two to four option cards, each with a one-line tradeoff; the recommended one is marked. | What are my choices? |
| 5 | Known limits | Two to four cards: what is unproven, not enforced, or still missing. | What could still go wrong? |
| 6 | Footer and answer bar | The change or report link, evidence (checks and the date reviewed), and the words to say to answer. A sticky bar repeats the recommendation and those words. | How do I act? |

Terms:

- **Cost of delay**: the value lost, or the pain that continues, for each unit
  of time the change waits.
- **Exposure**: the risk that persists while things stay as they are, and how
  it grows.

Lead sections 1 and 3 with business outcomes. Put file names and code detail
in section 5 or the footer.

## Diagram rules

- One flow per brief, left to right, at most six boxes, plain-language labels.
  Each box has a bold step name and a muted detail line.
- `.step.new` marks what this change adds, a plain `.step` is already in place,
  `.step.removed` is removed, and `.arrow.later` draws a dashed connection
  for later work.
  Keep the legend.
- Write the `aria-label` as one sentence that says the whole flow. It doubles
  as the spoken summary.
- The boxes stack vertically below 700px, so the diagram never scrolls
  sideways on a phone.

## Fill and publish (Firstmate)

1. The worker that produced the change drafts the brief, because it knows the
   change. Firstmate reviews it and publishes it, because only Claude sessions
   have the Artifact tool.
2. Copy the template to a scratch `.html` file outside every repository. Replace
   every `{{PLACEHOLDER}}`, remove unused optional sections and the guidance
   comments, and check that no placeholder remains:
   `grep -n '{{' brief.html` prints nothing.
3. Publish it as a Claude artifact. Artifacts are private by default; never
   switch a work brief to "anyone with the link".
4. Link it from the board item, with the one-line recommendation:

   ```sh
   quarterdeck board upsert <task-id> --brief-url <artifact-url> --recommendation "Merge"
   ```

   See [Keep the captain board current](keep-the-board-current.md).
5. Comments on a published brief artifact receive Claude Code's automatic
   instant reply. Every reply Firstmate writes itself starts with `FM: ` so
   the two can be told apart. When the comment thread reaches a Claude worker,
   that worker relays decisions to Firstmate; Firstmate then runs
   `quarterdeck board touch <task-id>` so the item stays on the desk.

**Fallback:** when the Artifact tool is unavailable, open the same file with
Lavish (`lavish-axi brief.html`) and use the Lavish session URL as the
`--brief-url`. A Lavish page is reachable only on the network that can reach
its host, so prefer the artifact.

Keep brief content in the home's data or the artifact, never in this
repository. The repository holds only the template.
