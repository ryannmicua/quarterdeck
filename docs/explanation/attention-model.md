# The attention model

Quarterdeck's default page is a current review surface. It uses local review
marks to determine which reports need review. The files it reads, optional
Firstmate snapshot, and read boundary are described in
[privacy and architecture](privacy-and-architecture.md).

## Reports needing review

Every discovered `data/<task-id>/report.md` without a reviewed mark for its
current bytes appears in a separate **Reports needing review** section on the
default page. This section is independent of the backlog attention cards, so
queued, finished, closed, and unlinked reports remain visible until reviewed.
With `--all`, each report appears once in **All scout reports**, where its
review state remains visible. Marking a report reviewed removes it from the
default section; changing its bytes makes the mark stop matching and returns
it to that section. Discarding a report removes it from the section without
marking it reviewed, and a report Firstmate marked **stale** (a one-off or
already decided) moves to a collapsed "Stale reports" section; the same
rules for a changed report apply to a discard. Unclassified reports are
current. Past captain calls marked stale likewise leave the desk, week and
parked bands, and a collapsed "Related to this session" section below the desk
ranks older reports and deferred calls that connect to the active session. See
[Keep the desk relevant](../how-to/keep-the-desk-relevant.md). See the [CLI reference](../reference/cli-and-config.md)
for report commands and the [privacy and architecture guide](privacy-and-architecture.md)
for how review marks are stored.

## What appears as a card

Each backlog record can appear in one attention section:

1. **Held for the captain:** the structured record has `held=yes` and
   `hold_kind=captain`, and it is not finished or closed. The card shows
   `hold_reason` and links to its report when one is recorded.
2. **Review-ready pull requests:** an open record has a GitHub pull-request
   link and an explicit review-ready flag or state. A linked report appears
   with this card.
3. **In-flight work:** the record's state says it is in flight, working, active,
   running, or in progress.
4. **Blocked for the captain or an external party:** the record is blocked and
   a structured `waiting_on` or `blocked_by` value identifies the
   captain or an external party.

When more than one condition matches, a record appears once, in the first
matching section above. Classified decisions also follow the shared
[decision relevance rules](../reference/attention-json.md#decisions). Secondmate
homes use the same rules and counts as their parent.

## What does not appear by default

Queued, finished, closed, and unclassified records are not backlog attention
cards. A historical `hold_kind` or `hold_reason` does not keep a record visible
after `held` is false. A report linked from a backlog card appears there only
while its item is held for the captain or marked review-ready. Independently,
any unreviewed report appears in the reports section. A quiet line may give
counts for queued and finished or closed backlog records.

Use `quarterdeck render --all` to include other backlog records and every
report, including reports that have already been reviewed. Decision relevance
still applies: background decisions remain collapsed and deferred decisions
remain hidden. Each report appears once with its review state.

## Counts

The page header counts match the cards in the four attention sections. A
separate total counts reports needing review. In `--all` mode, two additional
counts cover other backlog records and all scout reports. Per-home counts
include that home's local secondmates only in their own panel; the page header
totals all configured homes and secondmates.
