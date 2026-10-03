# The attention model

Quarterdeck's default page is a current review surface. It reads each selected
home's backlog, reports, and secondmate route file directly. It does not run
Firstmate scripts or commands.

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
matching section above. Secondmate homes use the same rules and counts as their
parent.

## What does not appear by default

Queued, finished, closed, and unclassified records are not attention cards.
A historical `hold_kind` or `hold_reason` does not keep a record
visible after `held` is false. Reports appear only when their backlog
record is still held for the captain or marked review-ready, so a report drops
off when its item is answered or closed. A quiet line may give counts for
queued and finished or closed records.

Use `quarterdeck render --all` to restore an exhaustive view with every
backlog record and report, including old and unlinked reports.

## Counts

The page header counts match the cards in the four attention sections. In
`--all` mode, two additional counts cover other backlog records and all
scout reports. Per-home counts include that home's local secondmates only in
their own panel; the page header totals all configured homes and secondmates.
