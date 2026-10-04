# The attention model

Quarterdeck's default page is a current review surface. It reads each selected
home's backlog, reports, and secondmate route file directly. It also compares
each report's content fingerprint with Quarterdeck's private review marks. It
does not run Firstmate scripts or commands.

## Reports needing review

Every discovered `data/<task-id>/report.md` without a reviewed mark for its
current bytes appears in a separate **Reports needing review** section. This
section is independent of the backlog attention cards, so queued, finished,
closed, and unlinked reports remain visible until they are reviewed. Marking a
report reviewed hides it from this section; editing it changes its sha256
fingerprint and returns it to the section. The same report IDs and state are
available through `quarterdeck reports list` and the `quarterdeck reports`
commands.

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

Queued, finished, closed, and unclassified records are not backlog attention
cards. A historical `hold_kind` or `hold_reason` does not keep a record visible
after `held` is false. A report linked from a backlog card appears there only
while its item is held for the captain or marked review-ready. Independently,
any unreviewed report appears in the reports section. A quiet line may give
counts for queued and finished or closed backlog records.

Use `quarterdeck render --all` to restore an exhaustive view with every
backlog record and report, including reports that have already been reviewed.

## Counts

The page header counts match the cards in the four attention sections and
includes a separate total for reports needing review. In `--all` mode, two
additional counts cover other backlog records and all scout reports. Per-home
counts include that home's local secondmates only in their own panel; the page
header totals all configured homes and secondmates.
