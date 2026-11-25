# Read the report

`quarterdeck stats` counts questions by kind, tag and project, so you can see
which sorts of questions you answer on the Desk and which you answer
somewhere else.

```sh
quarterdeck stats
quarterdeck stats --since 2026-11-01
quarterdeck stats --json
```

`--since` keeps questions saved on or after that UTC date.

## The table

The report prints one table for all questions, then one each by kind, by tag
and by project. A question counts under each of its tags, its item's tags
included, so the tag rows can add up to more than the total. A question with
no kind, tag or project counts under `unclassified`.

| Column | Meaning |
| --- | --- |
| Saved | Questions saved. |
| Desk | Answered on the Desk. |
| Chat, Review page, Elsewhere, Superseded | Answered somewhere else, one column for each answered-via value in the [config](../reference/cli-and-config.md#classification). |
| Open | Still waiting for an answer. |
| Closed | Closed without an answer. |
| Median wait | The middle wait from saved to answered, over every answered question. `none` when nothing is answered. |
| Matched | Desk answers that were the suggested answer, word for word. |

An answer given somewhere else comes from `quarterdeck mark ... answered --via`.
Quarterdeck records what it is told and never decides whether a question was
answered elsewhere.

## The JSON

`--json` prints the same numbers. The top level holds `since` (a date or
`null`), `via_values` (the answered-via columns, in order), `total`, and
`by_kind`, `by_tag` and `by_project`, each an object keyed by name. Every group
and the total have the same fields:

| Field | Meaning |
| --- | --- |
| `saved` | Questions saved. |
| `answered_desk` | Answered on the Desk. |
| `answered_elsewhere` | Answered somewhere else, all sources together. |
| `via` | The elsewhere count for each answered-via value. |
| `open`, `closed` | Questions still open, and closed. |
| `median_wait_seconds` | Median seconds from saved to answered, or `null`. |
| `median_wait_desk_seconds`, `median_wait_elsewhere_seconds` | The same median for each side. |
| `matched_suggestion` | Desk answers that matched the suggestion. |

To follow a trend, run the report with a later `--since` each time and compare.
