# Read the report

`quarterdeck stats` counts questions by kind, tag and project, so you can see
which sorts of questions you answer on the Desk and which you answer
somewhere else.

```sh
quarterdeck stats
quarterdeck stats --since 2026-11-01
quarterdeck stats --json
```

With `--since`, the report includes questions saved or marked answered on or
after that UTC date. Each answer source counts by its recorded date, so an
older question can count under Chat without adding to Saved. The all-time
report includes undated legacy answers under Elsewhere. A `--since` report
excludes undated answers because their dates are unknown.

## The table

The report prints one table for all questions, then one each by kind, by tag
and by project. A question counts under each of its tags, its item's tags
included, so the tag rows can add up to more than the total. A question with
no kind, tag or project counts under `(unclassified)`.

| Column | Meaning |
| --- | --- |
| Saved | Questions saved. |
| Desk | Answered on the Desk. |
| Chat, Review page, Elsewhere, Superseded | Questions marked answered through each of the four supported off-Desk sources. |
| Open | Still waiting for an answer. |
| Closed | Currently closed. |
| Median Desk wait | Median time from save to a Desk submission, shown in whole minutes or larger units. `none` when there are no Desk submissions to measure. |

Each question counts once per recorded source, even if it was marked through
that source more than once. A question can count in multiple source columns
and can also be Open or Closed. Legacy source names outside the fixed list
contribute to the JSON off-Desk total. For answers marked elsewhere, the timestamp
records when Quarterdeck learned of the answer. Only Desk submissions
contribute to the median wait.

## The JSON

`--json` includes the table's counts, the distinct off-Desk question count,
and the median Desk wait in seconds. The top level holds `since` (a date or
`null`), `via_values` (the answered-via columns, in order), `total`, and
`by_kind`, `by_tag` and `by_project`, each an object keyed by name. Every group
and the total have the same fields:

| Field | Meaning |
| --- | --- |
| `saved` | Questions saved. |
| `answered_desk` | Answered on the Desk. |
| `answered_elsewhere` | Distinct questions answered off the Desk, including legacy sources. |
| `via` | The question count for each of the four supported off-Desk sources. |
| `open`, `closed` | Questions still open, and closed. |
| `median_wait_desk_seconds` | Median seconds from save to a Desk submission, including fractional seconds, or `null`. |

To follow a trend, run the report with a later `--since` each time and compare.
