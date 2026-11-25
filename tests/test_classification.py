"""Classification fields, answer provenance and the stats report. Every item here is invented."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def saved_page(self) -> str:
        data = quarterdeck.read_store(quarterdeck.store_path())
        return quarterdeck.site_pages(data, "Quarterdeck", "index.html")["index.html"]

    def check_browser(self, page, script, *, widths=(1024,)) -> None:
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the classification browser tests")
        fixture = self.root / "browser-check.html"
        fixture.write_text(page.replace("</body>", """
<output id="browser-result">Incomplete</output>
<script>
const result = document.getElementById('browser-result');
const check = (condition, message) => { if (!condition) throw new Error(message); };
try {
""" + script + """
  result.textContent = 'PASS';
} catch (error) {
  result.textContent = error.message;
}
</script>
</body>"""), encoding="utf-8")
        for width in widths:
            with self.subTest(width=width):
                result = subprocess.run(
                    [chrome, "--headless", "--disable-background-networking", "--no-first-run",
                     f"--user-data-dir={self.root / 'browser'}", f"--window-size={width},800", "--dump-dom", fixture.as_uri()],
                    capture_output=True, text=True, timeout=30, check=True)
                marker = result.stdout.partition('<output id="browser-result">')[2].partition("</output>")[0]
                self.assertEqual(marker, "PASS", result.stderr)


class FieldTests(ClassificationCase):
    def test_items_and_questions_keep_their_classification(self) -> None:
        item_id = self.questions("Harbor choices", "--project", "Harbor", "--tag", "process", "--tag", "ui")
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
        self.check_browser(self.saved_page(), """
check(document.querySelectorAll('main select').length === 0, 'Unclassified items should have no selectors');
check(document.querySelectorAll('main .tags').length === 0, 'Unclassified items should have no tag labels');
""")

    def test_unknown_and_malformed_values_are_refused_with_the_allowed_list(self) -> None:
        cases = [
            (("--tag", "UI"), "item tag must be one lowercase word"),
            (("--tag", " ui "), "item tag must be one lowercase word"),
            (("--tag", "gossip"), "unknown item tag 'gossip'; allowed: product-design, ui, architecture"),
            (("--tag", "ui", "--tag", "bug", "--tag", "release", "--tag", "process"), "at most 3 tags"),
            (("--project", "x" * 41), "longer than 40"),
        ]
        for extra, message in cases:
            with self.subTest(extra=extra):
                code, _, err = self.run_cli("add", "--kind", "list", "--title", "T", "--description", "D",
                                            "--body-file", "-", *extra, stdin="one\n")
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        bad_bodies = [
            ([{**ASK[0], "kind": None}], "question 1 kind must be one lowercase word"),
            ([{**ASK[0], "kind": "PICK"}], "question 1 kind must be one lowercase word"),
            ([{**ASK[0], "tags": ["UI"]}], "question 1 tag must be one lowercase word"),
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

    def test_project_accepts_qualified_repository_names(self) -> None:
        item_id = self.questions("Repository decisions", "--project", "TideWorks/Harbor-tools")
        self.assertIn("Project: TideWorks/Harbor-tools", self.run_cli("show", item_id)[1])
        code, _, err = self.run_cli("classify", item_id, "--project", "QuayWorks/Dock.tools")
        self.assertEqual(code, 0, err)
        code, output, err = self.run_cli("stats", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(list(json.loads(output)["by_project"]), ["QuayWorks/Dock.tools"])

    def test_project_refuses_invalid_qualified_names(self) -> None:
        for project in ("a/b/c", "/harbor", "harbor/", "a//b", "a/b?c", "a\\b", "a/ b", "../harbor", "a/.."):
            with self.subTest(project=project):
                code, _, err = self.run_cli("add", "--kind", "list", "--title", "T", "--description", "D",
                                            "--body-file", "-", "--project", project, stdin="one\n")
                self.assertEqual(code, 2)
                self.assertIn("project must be a plain name or owner/name", err)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_tags_are_deduplicated(self) -> None:
        self.questions("Dupes", "--tag", "ui", "--tag", "ui")
        self.assertEqual(self.store()["items"][0]["tags"], ["ui"])

    def test_saved_classification_can_still_be_read_after_vocabulary_changes(self) -> None:
        self.questions(body=ASK[:1])
        self.edit_store(lambda d: d["items"][0].update(tags=["harbor-law"]))
        self.assertIn("Tags: harbor-law", self.run_cli("show", "Q1")[1])

    def test_item_commands_use_fixed_values_and_the_config_refuses_overrides(self) -> None:
        config = self.root / "quarterdeck.json"
        config.write_text(json.dumps({"classification": {"answered_via": ["phone"]}}), encoding="utf-8")
        code, _, err = self.run_cli("render", "--config", str(config))
        self.assertEqual(code, 2)
        self.assertIn("unsupported config key(s): classification", err)
        self.questions()
        code, _, err = self.run_cli("mark", "Q1.1", "answered", "--via", "phone")
        self.assertEqual(code, 2)
        self.assertIn("allowed: chat, review-page, elsewhere, superseded", err)

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
                 (("Q1", "--tag", "UI"), "item tag must be one lowercase word"),
                 (("Q1.1", "--tag", "UI"), "question tag must be one lowercase word"),
                 (("Q1.1", "--kind", "PICK"), "kind must be one lowercase word"),
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
    def test_desk_answers_record_the_desk_once_even_on_retry(self) -> None:
        self.questions()
        self.answer("Q1.1", "Merge")
        record = self.store()["items"][0]["answers"]["1"]
        self.assertEqual(record["answer_marks"], [{"via": "desk", "answered_at": record["answered_at"]}])
        self.answer("Q1.1", "Merge")
        self.assertEqual(self.store()["items"][0]["answers"]["1"], record)

    def test_marking_answered_needs_a_known_via(self) -> None:
        self.questions()
        code, _, err = self.run_cli("mark", "Q1.1", "answered")
        self.assertEqual((code, "needs --via" in err, "chat, review-page, elsewhere, superseded" in err), (2, True, True))
        code, _, err = self.run_cli("mark", "Q1.1", "answered", "--via", "CHAT")
        self.assertEqual((code, "via must be one lowercase word" in err), (2, True))
        code, _, err = self.run_cli("mark", "Q1.1", "answered", "--via", "desk")
        self.assertEqual((code, "unknown via 'desk'" in err), (2, True))
        code, _, err = self.run_cli("mark", "Q1.1", "closed", "--via", "chat")
        self.assertEqual((code, "goes with answered" in err), (2, True))
        self.assertNotIn("answers", self.store()["items"][0])

    def test_mark_answered_records_only_the_source_and_time(self) -> None:
        self.questions()
        at = "2026-11-03T09:00:00Z"
        with patch.object(quarterdeck, "now_text", return_value=at):
            code, out, err = self.run_cli("mark", "Q1.1", "answered", "--via", "review-page")
        self.assertEqual((code, out), (0, "Q1.1 Answered via review-page\n"), err)
        record = self.store()["items"][0]["answers"]["1"]
        self.assertEqual(record["state"], "answered")
        self.assertEqual(set(record), {"state", "revision", "answer_marks"})
        self.assertEqual(record["answer_marks"], [{"via": "review-page", "answered_at": at}])
        self.assertIn(f"Answered elsewhere: review-page ({at})", self.run_cli("show", "Q1")[1])

    def test_whole_item_mark_changes_only_open_questions(self) -> None:
        self.questions()
        self.answer("Q1.2", "Red")
        self.assertEqual(self.run_cli("mark", "Q1.3", "closed")[0], 0)
        before = self.store()["items"][0]["answers"]
        code, out, err = self.run_cli("mark", "Q1", "answered", "--via", "chat")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), ["Q1.1 Answered via chat", "Q1.2 skipped (already answered)", "Q1.3 skipped (already closed)"])
        answers = self.store()["items"][0]["answers"]
        self.assertEqual(answers["1"]["answer_marks"][0]["via"], "chat")
        self.assertEqual({n: answers[n] for n in before}, before)
        code, out, err = self.run_cli("mark", "Q1", "answered", "--via", "chat")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("skipped"), 3)
        self.assertEqual(self.store()["items"][0]["answers"], answers)

    def test_single_mark_reports_and_preserves_non_open_questions(self) -> None:
        self.questions()
        self.answer("Q1.1", "Merge")
        self.assertEqual(self.run_cli("mark", "Q1.2", "closed")[0], 0)
        before = self.store()
        for number, state in ((1, "answered"), (2, "closed")):
            code, out, err = self.run_cli("mark", f"Q1.{number}", "answered", "--via", "chat")
            self.assertEqual(code, 0, err)
            self.assertIn(f"Q1.{number} skipped (already {state})", out)
        self.assertEqual(self.store(), before)

    def test_answer_history_survives_close_reopen_and_reanswer(self) -> None:
        self.questions()
        events = [
            ("chat", "2026-11-02T00:00:00Z"),
            ("desk", "2026-11-03T00:00:00Z"),
            ("superseded", "2026-11-04T00:00:00Z"),
            ("chat", "2026-11-05T00:00:00Z"),
        ]
        for via, at in events:
            with patch.object(quarterdeck, "now_text", return_value=at):
                if via == "desk":
                    self.answer("Q1.1", "Hold")
                else:
                    self.assertEqual(self.run_cli("mark", "Q1.1", "answered", "--via", via)[0], 0)
            self.assertEqual(self.run_cli("mark", "Q1.1", "closed")[0], 0)
            self.assertEqual(self.run_cli("mark", "Q1.1", "open")[0], 0)
        record = self.store()["items"][0]["answers"]["1"]
        self.assertEqual(record["answer_marks"], [{"via": via, "answered_at": at} for via, at in events])
        self.assertEqual((record["answer"], record["answered_at"], record["delivery"]),
                         ("Hold", "2026-11-03T00:00:00Z", "cancelled"))
        total = json.loads(self.run_cli("stats", "--json")[1])["total"]
        self.assertEqual((total["answered_desk"], total["answered_elsewhere"], total["via"]["chat"]), (1, 1, 1))

    def test_reconciliation_cancels_pending_desk_delivery_and_displays_both_answers(self) -> None:
        self.questions()
        desk_at = "2026-11-03T00:00:00Z"
        chat_at = "2026-11-04T00:00:00Z"
        with patch.object(quarterdeck, "now_text", return_value=desk_at):
            self.answer("Q1.1", "Hold")
            self.answer("Q1.2", "Red")
            self.answer("Q1.3", "Yes")
            with patch.object(quarterdeck.subprocess, "run") as inbox:
                inbox.return_value.returncode = 0
                record = self.store()["items"][0]["answers"]["3"]
                quarterdeck.send_answer(quarterdeck.store_path(), self.root,
                                        {"part": "Q1.3", "answer": "Yes", "revision": record["revision"]})
        before = self.store()["items"][0]["answers"]
        self.assertEqual(self.run_cli("mark", "Q1", "open")[0], 0)
        opened = self.store()["items"][0]["answers"]["1"]
        with patch.object(quarterdeck, "now_text", return_value=chat_at):
            self.assertEqual(self.run_cli("mark", "Q1.1", "answered", "--via", "chat")[0], 0)
            self.assertEqual(self.run_cli("mark", "Q1", "answered", "--via", "chat")[0], 0)
        item = quarterdeck.read_store(quarterdeck.store_path())["items"][0]
        for number in ("1", "2"):
            record = item["answers"][number]
            for field in ("answer", "answered_at", "request_id"):
                self.assertEqual(record[field], before[number][field])
            self.assertEqual(record["delivery"], "cancelled")
            self.assertEqual(record["answer_marks"], [{"via": "desk", "answered_at": desk_at},
                                                      {"via": "chat", "answered_at": chat_at}])
        self.assertEqual(item["answers"]["3"]["delivery"], "sent")
        saved_page = self.saved_page()
        self.assertIn('href="q1.html"', saved_page.split("<h2>Answered</h2>")[1])
        self.assertNotIn('class="card-link', saved_page)
        shown = self.run_cli("show", "Q1")[1]
        self.assertIn(f"Answer: Hold ({desk_at}, Sending stopped)", shown)
        self.assertIn(f"Answered elsewhere: chat ({chat_at})", shown)
        old = item["answers"]["1"]
        payload = {"part": "Q1.1", "answer": "Hold", "revision": opened["revision"]}
        with patch.object(quarterdeck.subprocess, "run") as inbox:
            with self.assertRaises(quarterdeck.AnswerConflict):
                quarterdeck.send_answer(quarterdeck.store_path(), self.root, payload)
            payload["revision"] = old["revision"]
            retry = quarterdeck.send_answer(quarterdeck.store_path(), self.root, payload)
            self.assertEqual(retry["delivery"], "cancelled")
            self.assertEqual(self.run_cli("mark", "Q1.1", "open")[0], 0)
            payload["revision"] = self.store()["items"][0]["answers"]["1"]["revision"]
            retry = quarterdeck.send_answer(quarterdeck.store_path(), self.root, payload)
            self.assertEqual((retry["state"], retry["delivery"]), ("open", "cancelled"))
            inbox.assert_not_called()
            self.assertEqual(self.store()["items"][0]["answers"]["1"]["answer_marks"], old["answer_marks"])

            inbox.return_value.returncode = 0
            retry = quarterdeck.send_answer(quarterdeck.store_path(), self.root, {**payload, "answer": "Merge"})
        inbox.assert_called_once()
        self.assertNotEqual(inbox.call_args.args[0][3], old["request_id"])
        self.assertEqual(inbox.call_args.args[0][-1], "Q1.1 Merge")
        self.assertEqual(retry["delivery"], "sent")

    def test_undated_legacy_marks_survive_state_changes_and_desk_answers(self) -> None:
        with patch.object(quarterdeck, "now_text", return_value="2026-11-01T00:00:00Z"):
            self.questions()
        self.edit_store(lambda d: d["items"][0].update(answers={
            str(number): {"state": "answered", "revision": "a" * 32} for number in range(1, 4)
        }))
        self.assertEqual(self.run_cli("mark", "Q1.1", "closed")[0], 0)
        self.assertEqual(self.run_cli("mark", "Q1.2", "open")[0], 0)
        with patch.object(quarterdeck, "now_text", return_value="2026-11-02T00:00:00Z"):
            self.answer("Q1.3", "Yes")
            self.assertEqual(self.run_cli("mark", "Q1.2", "answered", "--via", "chat")[0], 0)
        answers = self.store()["items"][0]["answers"]
        for record in answers.values():
            self.assertEqual(record["answer_marks"][0], {"via": "elsewhere", "answered_at": None})
        all_time = json.loads(self.run_cli("stats", "--json")[1])["total"]
        recent = json.loads(self.run_cli("stats", "--json", "--since", "2026-11-01")[1])["total"]
        self.assertEqual((all_time["via"]["elsewhere"], recent["via"]["elsewhere"]), (3, 0))
        self.assertEqual((recent["via"]["chat"], recent["answered_desk"]), (1, 1))

    def test_legacy_marks_remain_readable_and_notes_stay_private(self) -> None:
        self.questions()
        self.edit_store(lambda d: d["items"][0].update(answers={
            "1": {"state": "answered", "revision": "a" * 32},
            "2": {"state": "answered", "revision": "b" * 32, "via": "chat",
                  "answered_at": "2026-11-01T00:00:00Z", "note": "Private excerpt"},
        }))
        stats = json.loads(self.run_cli("stats", "--json")[1])["total"]
        self.assertEqual(stats["via"]["elsewhere"], 1)
        self.assertEqual(stats["via"]["chat"], 1)
        shown = self.run_cli("show", "Q1")[1]
        self.assertIn("Answered elsewhere: chat", shown)
        self.assertNotIn("Private excerpt", shown)
        item = quarterdeck.read_store(quarterdeck.store_path())["items"][0]
        for served in (True, False):
            page = quarterdeck.item_page(item, "Quarterdeck", served=served)
            self.assertIn("Answered elsewhere · chat", page)
            self.assertIn('<time datetime="2026-11-01T00:00:00Z">', page)
            self.assertNotIn("Private excerpt", page)
        self.assertEqual(self.run_cli("mark", "Q1.2", "open")[0], 0)
        self.answer("Q1.2", "Red")
        marks = self.store()["items"][0]["answers"]["2"]["answer_marks"]
        self.assertEqual(marks[0], {"via": "chat", "answered_at": "2026-11-01T00:00:00Z"})
        self.assertEqual(marks[1]["via"], "desk")

    def test_malformed_answer_marks_are_rejected_on_read(self) -> None:
        self.questions()
        bad = [[], {}, ["chat"], [{"via": "Bad Word", "answered_at": "2026-11-01T00:00:00Z"}],
               [{"via": "chat", "answered_at": "yesterday"}], [{"via": "chat"}]]
        for marks in bad:
            self.edit_store(lambda d: d["items"][0].update(answers={
                "1": {"state": "answered", "revision": "b" * 32, "answer_marks": marks}}))
            self.assertEqual(self.run_cli("show", "Q1")[0], 2, marks)


class StatsTests(ClassificationCase):
    def seed(self) -> None:
        with patch.object(quarterdeck, "now_text", side_effect=[
            "2026-11-01T00:00:00Z", "2026-11-10T00:00:00Z", "2026-11-20T00:00:00Z",
            "2026-11-01T02:00:00Z", "2026-11-02T00:00:00Z",
            "2026-11-01T01:00:00Z", "2026-11-10T06:00:00Z",
        ]):
            self.questions("Harbor choices", "--project", "Harbor", "--tag", "process")
            self.questions("Quay choices", "--project", "Quay", body=ASK[:2])
            self.questions("Loose ends", body=[ASK[2]])
            self.answer("Q1.1", "Merge")
            self.answer("Q1.2", "Blue")
            self.assertEqual(self.run_cli("mark", "Q1.3", "answered", "--via", "chat")[0], 0)
            self.assertEqual(self.run_cli("mark", "Q2.1", "answered", "--via", "superseded")[0], 0)
        self.assertEqual(self.run_cli("mark", "Q2.2", "closed")[0], 0)

    def report(self, *extra: str) -> dict:
        code, out, err = self.run_cli("stats", "--json", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_totals_and_median_waits(self) -> None:
        self.seed()
        total = self.report()["total"]
        self.assertEqual(total, {
            "saved": 6, "answered_desk": 2, "answered_elsewhere": 2, "open": 1, "closed": 1,
            "via": {"chat": 1, "review-page": 0, "elsewhere": 0, "superseded": 1},
            "median_wait_desk_seconds": 13 * 3600,
        })

    def test_desk_median_keeps_half_seconds_in_json(self) -> None:
        with patch.object(quarterdeck, "now_text", side_effect=[
            "2026-11-01T00:00:00Z", "2026-11-01T00:00:01Z", "2026-11-01T00:00:02Z",
        ]):
            self.questions("Harbor choices", "--project", "Harbor", body=[ASK[0], ASK[0]])
            self.answer("Q1.1", "Merge")
            self.answer("Q1.2", "Hold")
        report = self.report()
        for row in (report["total"], report["by_kind"]["approve"], report["by_tag"]["release"],
                    report["by_project"]["Harbor"]):
            self.assertEqual(row["median_wait_desk_seconds"], 1.5)

    def test_groups_by_kind_tag_and_project(self) -> None:
        self.seed()
        report = self.report()
        self.assertEqual({k: v["saved"] for k, v in report["by_kind"].items()}, {"approve": 2, "pick": 2, "(unclassified)": 2})
        self.assertEqual(report["by_kind"]["approve"]["answered_desk"], 1)
        self.assertEqual(report["by_kind"]["approve"]["answered_elsewhere"], 1)
        # question tags plus item tags; the first item adds "process" to all three of its questions
        self.assertEqual({k: v["saved"] for k, v in report["by_tag"].items()},
                         {"process": 3, "product-design": 2, "release": 2, "ui": 2, "(unclassified)": 1})
        self.assertEqual({k: v["saved"] for k, v in report["by_project"].items()}, {"Harbor": 3, "Quay": 2, "(unclassified)": 1})
        self.assertEqual(list(report["by_project"])[-1], "(unclassified)")

    def test_since_keeps_questions_saved_on_or_after_the_date(self) -> None:
        self.seed()
        self.assertEqual(self.report("--since", "2026-11-10")["total"]["saved"], 3)
        self.assertEqual(self.report("--since", "2026-11-11")["total"]["saved"], 1)
        self.assertEqual(self.report("--since", "2026-11-21")["total"]["saved"], 0)
        code, _, err = self.run_cli("stats", "--since", "last week")
        self.assertEqual((code, "such as 2026-11-01" in err), (2, True))

    def test_since_includes_an_older_question_marked_then_closed_in_the_window(self) -> None:
        with patch.object(quarterdeck, "now_text", return_value="2026-10-14T00:00:00Z"):
            self.questions("October decisions", "--project", "Harbor", body=ASK[:1])
        with patch.object(quarterdeck, "now_text", return_value="2026-11-02T00:00:00Z"):
            self.assertEqual(self.run_cli("mark", "Q1.1", "answered", "--via", "chat")[0], 0)
        self.assertEqual(self.run_cli("mark", "Q1.1", "closed")[0], 0)
        report = self.report("--since", "2026-11-01")
        self.assertEqual((report["total"]["saved"], report["total"]["answered_elsewhere"]), (0, 1))
        self.assertEqual(report["by_project"]["Harbor"]["via"]["chat"], 1)
        self.assertIn("Harbor", self.run_cli("stats", "--since", "2026-11-01")[1])
        self.assertEqual(self.report("--since", "2026-11-03")["total"]["answered_elsewhere"], 0)

    def test_missing_classifications_stay_distinct_from_the_name_unclassified(self) -> None:
        self.questions("Named", "--project", "unclassified", body=ASK[2:])
        self.questions("Missing", body=ASK[2:])
        self.edit_store(lambda d: d["items"][0]["body"]["questions"][0].update(
            kind="unclassified", tags=["unclassified"]))
        report = self.report()
        for key in ("by_kind", "by_tag", "by_project"):
            self.assertEqual({name: row["saved"] for name, row in report[key].items()},
                             {"unclassified": 1, "(unclassified)": 1})
        lines = self.run_cli("stats")[1].splitlines()
        self.assertEqual(sum(line.split()[0] == "unclassified" for line in lines if line), 3)
        self.assertEqual(sum(line.split()[0] == "(unclassified)" for line in lines if line), 3)

    def test_legacy_sources_count_off_desk_without_becoming_report_columns(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", body=ASK[:1])
        self.edit_store(lambda d: d["items"][0].update(answers={
            "1": {"state": "closed", "revision": "a" * 32, "via": "closed", "answered_at": "2026-11-02T00:00:00Z"}
        }))
        report = self.report()
        columns = ["chat", "review-page", "elsewhere", "superseded"]
        self.assertEqual(report["via_values"], columns)
        for row in (report["total"], report["by_kind"]["approve"], report["by_tag"]["release"],
                    report["by_project"]["Harbor"]):
            self.assertEqual((row["answered_elsewhere"], row["closed"]), (1, 1))
            self.assertEqual(row["via"], dict.fromkeys(columns, 0))
        code, output, err = self.run_cli("stats")
        self.assertEqual(code, 0, err)
        for title in ("All", "Kind", "Tag", "Project"):
            heading = next(line for line in output.splitlines() if line.split()[:2] == [title, "Saved"])
            self.assertEqual(heading.split().count("Closed"), 1)

    def test_text_report_is_an_aligned_table(self) -> None:
        self.seed()
        code, out, _ = self.run_cli("stats")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "All saved questions")
        header = next(line for line in lines if line.startswith("Kind"))
        self.assertEqual(header.split(), ["Kind", "Saved", "Desk", "Chat", "Review", "page", "Elsewhere", "Superseded",
                                          "Open", "Closed", "Median", "Desk", "wait"])
        row = next(line for line in lines if line.startswith("approve"))
        self.assertEqual(len(row), len(header))
        self.assertIn("All questions", out)
        self.assertEqual(self.run_cli("stats", "--since", "2026-12-01")[1], "Question activity since 2026-12-01\nNo questions saved.\n")

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

    def test_several_projects_show_in_the_project_column_not_group_headings(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor")
        self.questions("Named project", "--project", "No project")
        self.questions("Loose ends")
        page = self.pages()["index.html"]
        self.assertNotIn('class="group-name"', page)
        self.assertEqual(re.findall(r'<td class="project">(.*?)</td>', page), ["", "No project", "Harbor"])
        self.assertEqual(page.count('<tr class="work next"'), 1)
        self.assertLess(page.index("work next"), page.index("Named project"))

    def test_more_prompts_add_no_desk_rows_or_prompt_text(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", body=ASK[:1])
        before = self.pages()["index.html"]
        self.questions("Second set", "--project", "Harbor", body=ASK * 3)
        after = self.pages()["index.html"]
        self.assertEqual(before.count('<tr class="work'), 1)
        self.assertEqual(after.count('<tr class="work'), 2)
        for prompt in [q["question"] for q in ASK]:
            self.assertNotIn(prompt, after.split("<h2>References</h2>")[0])

    def test_tags_show_as_quiet_labels_on_reference_rows_not_the_desk(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", "--tag", "process")
        self.add("resource", "Tide chart", "A chart.", "--url", "https://example.com/t", "--link-text", "Chart",
                 "--project", "Harbor", "--tag", "security-privacy")
        page = self.pages()["index.html"]
        self.assertNotIn("<li>process</li>", page.split("<h2>References</h2>")[0])
        self.assertIn("<li>process</li>", self.pages()["q1.html"])
        self.assertIn("<li>security privacy</li>", page)
        self.assertIn('<span class="project-name">Harbor</span>',
                      page.split("<h2>References</h2>")[1].split("</section>")[0])

    def test_filters_apply_to_references_and_answered(self) -> None:
        for number, tag in enumerate(("research", "bug"), 1):
            self.add("resource", f"Chart {number}", "A chart.", "--url", "https://example.com/t", "--link-text", "Chart",
                     "--project", "Harbor", "--tag", tag)
        for project, tag in (("All projects", "research"), ("QuayWorks/Dock.tools", "bug")):
            item_id = self.questions(project, "--project", project, "--tag", tag, body=ASK[2:])
            self.assertEqual(self.run_cli("mark", item_id, "answered", "--via", "chat")[0], 0)
        script = """
const dialog = document.querySelector('#library');
if (dialog) document.querySelector('#open-library').click();
const references = document.querySelector('[data-list="references-list"]');
const answered = document.querySelector('[data-list="answered-list"]');
const choose = (bar, name, value) => {
  const select = bar.querySelector('select[name="' + name + '"]');
  select.value = value;
  select.dispatchEvent(new Event('change', {bubbles: true}));
};
const links = list => [...document.querySelectorAll('#' + list + ' > li:not([hidden]) .lib-title')]
  .map(element => element.getAttribute('href')).join(',');
const referenceLinks = () => links('references-list');
const answeredLinks = () => links('answered-list');
check(!references.querySelector('[name="project"]'), 'References should only offer a tag filter');
choose(references, 'tag', 'bug');
check(referenceLinks() === 'q2.html', 'Reference tag filter should show only Q2');
check(answeredLinks() === 'q4.html,q3.html', 'Reference filter affected Answered');
if (dialog) document.querySelector('#tab-answered').click();
check([...answered.querySelector('[name="project"]').options].map(option => option.textContent).join(',') ===
  '(All projects),All projects,QuayWorks/Dock.tools', 'The reset choice and literal project should have distinct labels');
choose(answered, 'project', 'QuayWorks/Dock.tools');
check(answeredLinks() === 'q4.html', 'The repository filter should show only Q4');
choose(answered, 'project', 'All projects');
check(answeredLinks() === 'q3.html', 'Answered project filter should show only Q3');
choose(answered, 'tag', 'bug');
check(!answeredLinks(), 'Incompatible Answered filters should hide both items');
const status = document.querySelector(dialog ? '#panel-answered [role="status"]' : '#answered-list-empty');
check(status.checkVisibility() && status.textContent, 'Answered should show an empty state');
check(referenceLinks() === 'q2.html', 'Answered filters affected References');
choose(answered, 'tag', '');
check(answeredLinks() === 'q3.html', 'Clearing the tag should restore Q3');
choose(answered, 'project', '');
check(answeredLinks() === 'q4.html,q3.html', 'Resetting the project should restore both answered items');
if (dialog) {
  choose(answered, 'tag', 'bug');
  const search = document.querySelector('#search-answered');
  search.value = 'Q3';
  search.dispatchEvent(new Event('input', {bubbles: true}));
  check(!answeredLinks(), 'ID search ignored the tag filter');
  choose(answered, 'tag', '');
  check(answeredLinks() === 'q3.html', 'Changing the tag reset the ID search');
  document.querySelector('#tab-references').click();
}
choose(references, 'tag', '');
check(referenceLinks() === 'q2.html,q1.html', 'Clearing the reference filter should restore both items');
"""
        data = quarterdeck.read_store(quarterdeck.store_path())
        for served in (True, False):
            with self.subTest(served=served):
                page = quarterdeck.list_page(data, "Quarterdeck", served=served)
                self.check_browser(page, script, widths=(390, 1024))

    def test_uniformly_tagged_lists_keep_the_tag_selector(self) -> None:
        for title in ("Tide chart", "Harbor chart"):
            self.add("resource", title, "A chart.", "--url", "https://example.com/t", "--link-text", "Chart",
                     "--tag", "bug")
        for title in ("Quay decision", "Dock decision"):
            item_id = self.questions(title, body=[{**ASK[2], "tags": ["bug"]}])
            self.assertEqual(self.run_cli("mark", item_id, "answered", "--via", "chat")[0], 0)
        self.check_browser(self.saved_page(), """
const cases = [
  ['references-list', 'q2.html,q1.html'],
  ['answered-list', 'q4.html,q3.html']
];
for (const [id, expected] of cases) {
  const select = document.querySelector(`[data-list="${id}"] select`);
  check(select?.checkVisibility(), `${id} should offer its shared tag`);
  check([...select.options].map(option => option.value).join(',') === ',bug', `${id} should offer only bug and reset`);
  select.value = 'bug';
  select.dispatchEvent(new Event('change', {bubbles: true}));
  const visible = [...document.getElementById(id).querySelectorAll('.lib-title')].filter(entry => entry.checkVisibility());
  check(visible.map(entry => entry.getAttribute('href')).join(',') === expected, `Both ${id} entries should remain visible`);
}
""")

    def test_reconciled_answer_stays_out_of_the_browser_send_queue(self) -> None:
        self.questions()
        with patch.object(quarterdeck, "now_text", return_value="2026-11-03T00:00:00Z"):
            self.answer("Q1.1", "Hold")
        self.assertEqual(self.run_cli("mark", "Q1.1", "open")[0], 0)
        with patch.object(quarterdeck, "now_text", return_value="2026-11-04T00:00:00Z"):
            self.assertEqual(self.run_cli("mark", "Q1.1", "answered", "--via", "chat")[0], 0)
        item = quarterdeck.read_store(quarterdeck.store_path())["items"][0]
        saved_answer = """
const question = document.getElementById('Q1.1');
const saved = question.querySelector('.saved-answer');
const answer = saved.querySelector('.record');
const delivery = saved.querySelector('.delivery');
check(answer.checkVisibility() && answer.innerText === 'Hold', 'The saved answer should visibly read Hold');
check(delivery.checkVisibility() && delivery.innerText === 'Sending stopped', 'The saved answer should show Sending stopped');
check(saved.querySelector('time').dateTime === '2026-11-03T00:00:00Z', 'The Desk answer date should be retained');
const source = question.querySelector('.answer-source');
check(source.checkVisibility() && source.innerText.startsWith('Answered elsewhere · chat'), 'The off-Desk source should be visible');
check(source.querySelector('time').dateTime === '2026-11-04T00:00:00Z', 'The source should show the reconciliation date');
"""
        queue_check = """
const send = document.getElementById('send');
check(send.disabled, 'A reconciled Desk answer should not enable sending');
const field = document.getElementById('answer-Q1.2');
field.value = 'Red';
field.dispatchEvent(new Event('input', {bubbles: true}));
check(!send.disabled && send.textContent === 'Send 1 answer', 'Only the new draft should be queued');
"""
        for served in (True, False):
            with self.subTest(served=served):
                page = quarterdeck.item_page(item, "Quarterdeck", served=served)
                self.check_browser(page, saved_answer + (queue_check if served else ""))

    def test_primary_card_and_anchor_follow_newest_open_item_across_groups(self) -> None:
        self.questions("Old Harbor", "--project", "Harbor", body=ASK[:1])
        self.questions("Open Quay", "--project", "Quay", body=ASK[:1])
        self.questions("New Harbor", "--project", "Harbor", body=ASK[:1])
        self.answer("Q3.1", "Merge")
        page = self.pages()["index.html"]
        self.assertEqual(re.findall(r'<tr class="work next"><th scope="row">.*?<a class="card-link" href="([^"]+)"', page), ["q2.html#Q2.1"])
        self.assertLess(page.index("New Harbor"), page.index("Open Quay"))

    def test_item_page_shows_project_tags_kind_and_question_tags(self) -> None:
        self.questions("Harbor choices", "--project", "Harbor", "--tag", "process")
        page = self.pages()["q1.html"]
        self.assertIn("Questions · Harbor · saved", page)
        self.assertIn("<li>process</li>", page)
        self.assertIn("<span>Approve</span>", page)
        self.assertIn("<li>product design</li>", page)


if __name__ == "__main__":
    unittest.main()
