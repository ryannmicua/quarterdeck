"""Classification fields, answer provenance and the stats report. Every item here is invented."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_quarterdeck as base  # noqa: E402

quarterdeck = base.quarterdeck

ASK = [
    {"question": "Merge the tide tables now?", "context": "Checks are green.", "options": ["Merge", "Hold"],
     "suggestion": "Merge", "kind": "approve", "tags": ["release"]},
    {"question": "Which colour for the quay?", "context": "Red or blue.", "options": ["Red", "Blue"],
     "kind": "pick", "tags": ["ui", "product-design"]},
    {"question": "Plain question?", "context": "No classification.", "options": ["Yes", "No"]},
]


class ClassificationCase(base.StoreCase):
    def questions(self, title="Harbor choices", *extra, body=ASK) -> str:
        return self.add("questions", title, "Saved to decide later.", *extra, "--body-file", "-", stdin=json.dumps(body))

    def write_config(self, classification: dict) -> Path:
        path = self.root / "config" / "quarterdeck.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"classification": classification}), encoding="utf-8")
        return path

    def answer(self, part: str, text: str) -> None:
        item_id, number = part.split(".")
        data = quarterdeck.read_store(quarterdeck.store_path())
        record = quarterdeck.part_record(quarterdeck.find_item(data, item_id), int(number))
        quarterdeck.send_answer(quarterdeck.store_path(), None,
                                {"part": part, "answer": text, "revision": record.get("revision", "")})

    def edit_store(self, change) -> None:
        data = self.store()
        change(data)
        quarterdeck.write_store(quarterdeck.store_path(), data)


class FieldTests(ClassificationCase):
    def test_items_and_questions_keep_their_classification(self) -> None:
        item_id = self.questions("Harbor choices", "--project", "Harbor", "--tag", "process", "--tag", "UI")
        item = self.store()["items"][0]
        self.assertEqual((item["project"], item["tags"]), ("Harbor", ["process", "ui"]))
        first, second, third = item["body"]["questions"]
        self.assertEqual((first["kind"], first["tags"]), ("approve", ["release"]))
        self.assertEqual(second["tags"], ["ui", "product-design"])
        self.assertNotIn("kind", third)
        self.assertNotIn("tags", third)
        shown = self.run_cli("show", item_id)[1]
        self.assertIn("Project: Harbor", shown)
        self.assertIn("Tags: process, ui", shown)
        self.assertIn("Kind: approve", shown)

    def test_items_without_the_fields_keep_working(self) -> None:
        self.add_all_kinds()
        for item in self.store()["items"]:
            self.assertNotIn("project", item)
            self.assertNotIn("tags", item)
        self.assertEqual(self.run_cli("list")[0], 0)
        page = quarterdeck.site_pages(quarterdeck.read_store(quarterdeck.store_path()), "Quarterdeck", "index.html")["index.html"]
        self.assertNotIn('class="tags"', page)
        self.assertNotIn("filters", page.split("</style>")[1])

    def test_unknown_and_malformed_values_are_refused_with_the_allowed_list(self) -> None:
        cases = [
            (("--tag", "gossip"), "unknown item tag 'gossip'; allowed: product-design, ui, architecture"),
            (("--tag", "ui", "--tag", "bug", "--tag", "release", "--tag", "process"), "at most 3 tags"),
            (("--project", "a/b"), "project must be a short word or name"),
            (("--project", "x" * 41), "longer than 40"),
        ]
        for extra, message in cases:
            with self.subTest(extra=extra):
                code, _, err = self.run_cli("add", "--kind", "list", "--title", "T", "--description", "D",
                                            "--body-file", "-", *extra, stdin="one\n")
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        bad_bodies = [
            ([{**ASK[0], "kind": "gossip"}], "unknown question 1 kind 'gossip'; allowed: pick, confirm, rule"),
            ([{**ASK[0], "tags": ["nope"]}], "unknown question 1 tag 'nope'"),
            ([{**ASK[0], "tags": "ui"}], "question 1 tags must be a list"),
        ]
        for body, message in bad_bodies:
            with self.subTest(body=body):
                code, _, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                            "--body-file", "-", stdin=json.dumps(body))
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_tags_are_deduplicated(self) -> None:
        self.questions("Dupes", "--tag", "ui", "--tag", "UI")
        self.assertEqual(self.store()["items"][0]["tags"], ["ui"])

    def test_the_config_replaces_the_default_lists(self) -> None:
        self.write_config({"kinds": ["vote"], "tags": ["harbor-law"], "answered_via": ["phone"]})
        code, _, err = self.run_cli("add", "--kind", "list", "--title", "T", "--description", "D", "--body-file", "-",
                                    "--tag", "ui", stdin="one\n")
        self.assertEqual((code, "allowed: harbor-law" in err), (2, True))
        self.questions("Custom", "--tag", "harbor-law", body=[{**ASK[0], "kind": "vote", "tags": []}])
        self.assertEqual(self.run_cli("mark", "Q1.1", "answered", "--via", "phone")[0], 0)
        code, _, err = self.run_cli("mark", "Q1.1", "open")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("mark", "Q1.1", "answered", "--via", "chat")
        self.assertEqual((code, "allowed: phone" in err), (2, True))

    def test_a_missing_classification_section_uses_the_defaults_and_a_bad_one_is_refused(self) -> None:
        self.assertEqual(self.questions().startswith("Q"), True)
        for bad, message in (({"tags": []}, "must be a list of different lowercase words"),
                             ({"tags": ["Ui"]}, "lowercase words"),
                             ({"tags": ["ui", "ui"]}, "different"),
                             ({"answered_via": ["desk"]}, "must not list desk"),
                             ({"moods": ["calm"]}, "unsupported config classification key")):
            with self.subTest(bad=bad):
                self.write_config(bad)
                code, _, err = self.run_cli("add", "--kind", "list", "--title", "T", "--description", "D",
                                            "--body-file", "-", stdin="one\n")
                self.assertEqual(code, 2)
                self.assertIn(message, err)

    def test_the_store_accepts_values_the_config_no_longer_lists(self) -> None:
        self.write_config({"tags": ["harbor-law"]})
        self.questions("Old", "--tag", "harbor-law", body=ASK[2:])
        self.write_config({"tags": ["other"]})
        self.assertEqual(self.run_cli("show", "Q1")[0], 0)

    def test_a_damaged_classification_in_the_store_is_reported(self) -> None:
        self.questions()
        for change in (lambda i: i.update(tags=[]), lambda i: i.update(project=""), lambda i: i.update(tags=["Bad Tag"])):
            self.edit_store(lambda d: d["items"][0].update(project="Harbor"))
            self.edit_store(lambda d: change(d["items"][0]))
            self.assertEqual(self.run_cli("show", "Q1")[0], 2)
            self.edit_store(lambda d: [d["items"][0].pop(k, None) for k in ("project", "tags")])
        self.assertEqual(self.run_cli("show", "Q1")[0], 0)


class ClassifyTests(ClassificationCase):
    def test_classify_replaces_an_items_project_and_tags_only(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", "--tag", "process")
        self.answer("Q1.1", "Merge")
        before = self.store()["items"][0]
        self.assertEqual(self.run_cli("classify", "q1", "--project", "Quay", "--tag", "bug", "--tag", "release")[0], 0)
        after = self.store()["items"][0]
        self.assertEqual((after["project"], after["tags"]), ("Quay", ["bug", "release"]))
        self.assertEqual(after["body"], before["body"])
        self.assertEqual(after["answers"], before["answers"])
        self.assertEqual(self.run_cli("classify", "Q1", "--project", "Dock")[0], 0)
        self.assertEqual(self.store()["items"][0]["tags"], ["bug", "release"])

    def test_classify_sets_one_questions_kind_and_tags(self) -> None:
        self.questions()
        self.answer("Q1.3", "Yes")
        before = self.store()["items"][0]
        self.assertEqual(self.run_cli("classify", "Q1.3", "--kind", "confirm", "--tag", "terminology")[0], 0)
        third = self.store()["items"][0]["body"]["questions"][2]
        self.assertEqual((third["kind"], third["tags"]), ("confirm", ["terminology"]))
        self.assertEqual(self.run_cli("classify", "Q1.1", "--tag", "bug")[0], 0)
        data = self.store()["items"][0]
        self.assertEqual(data["body"]["questions"][0]["kind"], "approve")
        self.assertEqual(data["body"]["questions"][0]["tags"], ["bug"])
        self.assertEqual(data["answers"], before["answers"])
        self.assertEqual(data["body"]["questions"][1], before["body"]["questions"][1])

    def test_classify_refusals_leave_the_store_alone(self) -> None:
        self.questions()
        self.add("resource", "Link", "A link.", "--url", "https://example.com/x", "--link-text", "Link")
        before = self.store()
        cases = [(("Q1",), "give at least one of"),
                 (("Q1", "--kind", "pick"), "kind belongs to one question"),
                 (("Q1.1", "--project", "Harbor"), "project belongs to the whole item"),
                 (("Q1.9", "--kind", "pick"), "Unknown question"),
                 (("Q1.1", "--kind", "gossip"), "allowed: pick, confirm"),
                 (("Q1", "--tag", "gossip"), "allowed: product-design"),
                 (("Q9", "--project", "x"), "no item Q9"),
                 (("Q2.1", "--kind", "pick"), "Choose a questions item")]
        for argv, message in cases:
            with self.subTest(argv=argv):
                code, _, err = self.run_cli("classify", *argv)
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        self.assertEqual(self.store(), before)
        self.assertEqual(self.run_cli("classify", "Q2", "--project", "Harbor", "--tag", "research")[0], 0)


class AnsweredViaTests(ClassificationCase):
    def test_desk_answers_record_the_desk(self) -> None:
        self.questions()
        self.answer("Q1.1", "Merge")
        record = self.store()["items"][0]["answers"]["1"]
        self.assertEqual(record["via"], "desk")
        self.assertEqual(self.run_cli("show", "Q1")[0], 0)

    def test_marking_answered_needs_a_known_via(self) -> None:
        self.questions()
        code, _, err = self.run_cli("mark", "Q1.1", "answered")
        self.assertEqual((code, "needs --via" in err, "chat, review-page, elsewhere, superseded" in err), (2, True, True))
        code, _, err = self.run_cli("mark", "Q1.1", "answered", "--via", "desk")
        self.assertEqual((code, "unknown via 'desk'" in err), (2, True))
        code, _, err = self.run_cli("mark", "Q1.1", "closed", "--via", "chat")
        self.assertEqual((code, "go with answered" in err), (2, True))
        self.assertNotIn("answers", self.store()["items"][0])

    def test_mark_answered_records_where_when_and_why(self) -> None:
        self.questions()
        code, out, err = self.run_cli("mark", "Q1.1", "answered", "--via", "review-page", "--note", "Chose Merge on the review page")
        self.assertEqual((code, out), (0, "Q1.1 Answered via review-page\n"), err)
        record = self.store()["items"][0]["answers"]["1"]
        self.assertEqual((record["state"], record["via"], record["note"]), ("answered", "review-page", "Chose Merge on the review page"))
        self.assertRegex(record["answered_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("answer", record)
        shown = self.run_cli("show", "Q1")[1]
        self.assertIn("Answered elsewhere: review-page", shown)
        self.assertIn("Note: Chose Merge on the review page", shown)

    def test_marking_a_whole_item_skips_questions_already_answered(self) -> None:
        self.questions()
        self.answer("Q1.2", "Red")
        self.assertEqual(self.run_cli("mark", "Q1", "answered", "--via", "chat")[0], 0)
        answers = self.store()["items"][0]["answers"]
        self.assertEqual({n: quarterdeck.answer_via(r) for n, r in answers.items()}, {"1": "chat", "2": "desk", "3": "chat"})
        self.assertEqual(answers["2"]["answer"], "Red")
        code, _, err = self.run_cli("mark", "Q1", "answered", "--via", "chat")
        self.assertEqual((code, "already answered" in err), (2, True))

    def test_a_question_answered_on_the_desk_is_not_overwritten(self) -> None:
        self.questions()
        self.answer("Q1.1", "Merge")
        before = self.store()
        code, _, err = self.run_cli("mark", "Q1.1", "answered", "--via", "chat")
        self.assertEqual((code, "mark it open first" in err), (2, True))
        self.assertEqual(self.store(), before)

    def test_reopening_keeps_the_record_and_answering_again_replaces_it(self) -> None:
        self.questions()
        self.run_cli("mark", "Q1.1", "answered", "--via", "chat", "--note", "Told in chat")
        self.run_cli("mark", "Q1.1", "closed")
        record = self.store()["items"][0]["answers"]["1"]
        self.assertEqual((record["state"], record["via"], record["note"]), ("closed", "chat", "Told in chat"))
        self.run_cli("mark", "Q1.1", "open")
        self.assertEqual(self.run_cli("mark", "Q1.1", "answered", "--via", "superseded")[0], 0)
        record = self.store()["items"][0]["answers"]["1"]
        self.assertEqual((record["via"], "note" in record), ("superseded", False))

    def test_a_desk_answer_replaces_an_elsewhere_record(self) -> None:
        self.questions()
        self.run_cli("mark", "Q1.1", "answered", "--via", "chat", "--note", "Told in chat")
        self.answer("Q1.1", "Hold")
        record = self.store()["items"][0]["answers"]["1"]
        self.assertEqual((record["via"], record["answer"], "note" in record), ("desk", "Hold", False))

    def test_a_bare_mark_from_before_counts_as_answered_elsewhere(self) -> None:
        self.questions()
        self.edit_store(lambda d: d["items"][0].update(answers={"1": {"state": "answered", "revision": "a" * 32}}))
        self.assertEqual(quarterdeck.answer_via(self.store()["items"][0]["answers"]["1"]), "elsewhere")
        self.assertEqual(self.run_cli("show", "Q1")[0], 0)

    def test_the_page_shows_answered_elsewhere_with_its_note(self) -> None:
        self.questions()
        self.run_cli("mark", "Q1.1", "answered", "--via", "chat", "--note", "Told in chat <b>")
        data = quarterdeck.read_store(quarterdeck.store_path())
        page = quarterdeck.item_page(data["items"][0], "Quarterdeck", served=True)
        section = re.search(r'<section class="decision" id="Q1.1".*?</section>', page, re.S)[0]
        self.assertIn('<span class="badge">Answered</span>', section)
        self.assertIn("Answered elsewhere", section)
        self.assertIn("Told in chat &lt;b&gt;", section)
        self.assertRegex(section, r'<time datetime="\d{4}-[^"]+Z">')
        self.assertNotIn("Sent", section)
        rendered = quarterdeck.item_page(data["items"][0], "Quarterdeck", served=False)
        self.assertIn("Answered elsewhere", rendered)

    def test_a_stored_via_record_is_validated(self) -> None:
        self.questions()
        self.run_cli("mark", "Q1.1", "answered", "--via", "chat")
        bad = [{"answer": "x"}, {"via": "Bad Word"}, {"note": ""}, {"answered_at": "yesterday"}]
        for change in bad:
            self.edit_store(lambda d: d["items"][0]["answers"]["1"].update(change))
            self.assertEqual(self.run_cli("show", "Q1")[0], 2, change)
            self.edit_store(lambda d: d["items"][0].update(answers={"1": {
                "state": "answered", "revision": "b" * 32, "via": "chat", "answered_at": "2026-11-01T00:00:00Z"}}))


class StatsTests(ClassificationCase):
    def seed(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", "--tag", "process")
        self.questions("Quay choices", "--project", "Quay", body=ASK[:2])
        self.questions("Loose ends", body=[ASK[2]])
        self.answer("Q1.1", "Merge")  # matched the suggestion
        self.answer("Q1.2", "Blue")
        self.run_cli("mark", "Q1.3", "answered", "--via", "chat")
        self.run_cli("mark", "Q2.1", "answered", "--via", "superseded")
        self.run_cli("mark", "Q2.2", "closed")

        def times(data):
            for item, saved in zip(data["items"], ("2026-11-01T00:00:00Z", "2026-11-10T00:00:00Z", "2026-11-20T00:00:00Z")):
                item["saved_at"] = saved
            base_answers = data["items"][0]["answers"]
            base_answers["1"]["answered_at"] = "2026-11-01T02:00:00Z"
            base_answers["2"]["answered_at"] = "2026-11-02T00:00:00Z"
            base_answers["3"]["answered_at"] = "2026-11-01T01:00:00Z"
            data["items"][1]["answers"]["1"]["answered_at"] = "2026-11-10T06:00:00Z"
        self.edit_store(times)

    def report(self, *extra: str) -> dict:
        code, out, err = self.run_cli("stats", "--json", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_totals_and_median_waits(self) -> None:
        self.seed()
        total = self.report()["total"]
        self.assertEqual({k: total[k] for k in ("saved", "answered_desk", "answered_elsewhere", "open", "closed", "matched_suggestion")},
                         {"saved": 6, "answered_desk": 2, "answered_elsewhere": 2, "open": 1, "closed": 1, "matched_suggestion": 1})
        self.assertEqual(total["via"], {"chat": 1, "review-page": 0, "elsewhere": 0, "superseded": 1})
        # waits: 2h, 24h, 1h, 6h -> median of 1h 2h 6h 24h is 4h
        self.assertEqual(total["median_wait_seconds"], 4 * 3600)
        self.assertEqual(total["median_wait_desk_seconds"], 13 * 3600)
        self.assertEqual(total["median_wait_elsewhere_seconds"], 3.5 * 3600)

    def test_groups_by_kind_tag_and_project(self) -> None:
        self.seed()
        report = self.report()
        self.assertEqual({k: v["saved"] for k, v in report["by_kind"].items()}, {"approve": 2, "pick": 2, "unclassified": 2})
        self.assertEqual(report["by_kind"]["approve"]["answered_desk"], 1)
        self.assertEqual(report["by_kind"]["approve"]["answered_elsewhere"], 1)
        # question tags plus item tags; the first item adds "process" to all three of its questions
        self.assertEqual({k: v["saved"] for k, v in report["by_tag"].items()},
                         {"process": 3, "product-design": 2, "release": 2, "ui": 2, "unclassified": 1})
        self.assertEqual({k: v["saved"] for k, v in report["by_project"].items()}, {"Harbor": 3, "Quay": 2, "unclassified": 1})
        self.assertEqual(list(report["by_project"])[-1], "unclassified")

    def test_since_keeps_questions_saved_on_or_after_the_date(self) -> None:
        self.seed()
        self.assertEqual(self.report("--since", "2026-11-10")["total"]["saved"], 3)
        self.assertEqual(self.report("--since", "2026-11-11")["total"]["saved"], 1)
        self.assertEqual(self.report("--since", "2026-11-21")["total"]["saved"], 0)
        code, _, err = self.run_cli("stats", "--since", "last week")
        self.assertEqual((code, "such as 2026-11-01" in err), (2, True))

    def test_text_report_is_an_aligned_table(self) -> None:
        self.seed()
        code, out, _ = self.run_cli("stats")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "All saved questions")
        header = next(line for line in lines if line.startswith("Kind"))
        self.assertEqual(header.split(), ["Kind", "Saved", "Desk", "Chat", "Review", "page", "Elsewhere", "Superseded",
                                          "Open", "Closed", "Median", "wait", "Matched"])
        row = next(line for line in lines if line.startswith("approve"))
        self.assertEqual(len(row), len(header))
        self.assertIn("All questions", out)
        self.assertEqual(self.run_cli("stats", "--since", "2026-12-01")[1], "Questions saved since 2026-12-01\nNo questions saved.\n")

    def test_an_empty_store_reports_no_questions(self) -> None:
        self.assertEqual(self.run_cli("stats")[1], "All saved questions\nNo questions saved.\n")
        self.assertEqual(self.report()["total"]["saved"], 0)

    def test_wait_text(self) -> None:
        self.assertEqual([quarterdeck.wait_text(v) for v in (None, 0, 90, 3660, 90000)], ["none", "0m", "1m", "1h 1m", "1d 1h"])


class PageTests(ClassificationCase):
    def pages(self):
        return quarterdeck.site_pages(quarterdeck.read_store(quarterdeck.store_path()), "Quarterdeck", "index.html")

    def test_one_project_shows_on_each_card_without_group_headings(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", "--tag", "process")
        self.questions("More choices", "--project", "Harbor", body=ASK[:1])
        page = self.pages()["index.html"]
        self.assertEqual(page.count('<span class="project-name">Harbor</span>'), 2)
        self.assertNotIn('class="group-name"', page)

    def test_several_projects_group_the_desk_with_plain_headings(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor")
        self.questions("Quay choices", "--project", "Quay")
        self.questions("Loose ends")
        page = self.pages()["index.html"]
        headings = re.findall(r'<h2 class="group-name">(.*?)</h2>', page)
        self.assertEqual(headings, ["No project", "Quay", "Harbor"])
        self.assertNotIn('class="project-name"', page)
        self.assertEqual(page.count('<article class="work next"'), 1)
        self.assertLess(page.index("work next"), page.index("Quay choices"))

    def test_tags_show_as_quiet_labels_on_cards_and_reference_rows(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", "--tag", "process")
        self.add("resource", "Tide chart", "A chart.", "--url", "https://example.com/t", "--link-text", "Chart",
                 "--project", "Harbor", "--tag", "security-privacy")
        page = self.pages()["index.html"]
        self.assertIn("<li>process</li>", page)
        self.assertIn("<li>product design</li>", page)
        self.assertIn("<li>security privacy</li>", page)
        self.assertIn('<th scope="col">Project</th>', page)
        self.assertIn('<td class="project">Harbor</td>', page)

    def test_lists_filter_by_project_and_tag_only_when_there_is_something_to_filter(self) -> None:
        for number, project in enumerate(("Harbor", "Quay"), 1):
            self.add("resource", f"Chart {number}", "A chart.", "--url", "https://example.com/t", "--link-text", "Chart",
                     "--project", project, "--tag", "research" if number == 1 else "bug")
        page = self.pages()["index.html"]
        bar = re.search(r'<div class="filters" data-list="references-list">(.*?)</div>', page, re.S)[1]
        self.assertIn('<option value="Harbor">Harbor</option>', bar)
        self.assertIn('<option value="bug">bug</option>', bar)
        self.assertIn("All projects", bar)
        self.assertIn("All tags", bar)
        self.assertEqual(page.count("data-project="), 2)
        self.assertIn("Nothing matches these filters.", page)
        self.assertIn("document.querySelectorAll('.filters')", page)
        self.run_cli("remove", "Q2")
        self.assertNotIn('class="filters"', self.pages()["index.html"])

    def test_item_page_shows_project_tags_kind_and_question_tags(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", "--tag", "process")
        page = self.pages()["q1.html"]
        self.assertIn("Questions · Harbor · saved", page)
        self.assertIn("<li>process</li>", page)
        self.assertIn("<span>Approve</span>", page)
        self.assertIn("<li>product design</li>", page)

    def test_answered_questions_filter_too(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", body=ASK[:1])
        self.questions("Quay choices", "--project", "Quay", body=ASK[:1])
        self.run_cli("mark", "Q1", "answered", "--via", "chat")
        self.run_cli("mark", "Q2", "answered", "--via", "chat")
        page = self.pages()["index.html"]
        self.assertIn('<div class="filters" data-list="reviewed-list">', page)
        self.assertIn('<div class="desk" id="reviewed-list">', page)


if __name__ == "__main__":
    unittest.main()
