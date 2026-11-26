import json
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quarterdeck  # noqa: E402
from test_quarterdeck import StoreCase  # noqa: E402


def anchors(html: str) -> list[tuple[str, str]]:
    class Anchors(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.found: list[tuple[str, str]] = []
            self.href: str | None = None

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.href = dict(attrs).get("href")
                self.found.append((self.href, ""))

        def handle_data(self, data):
            if self.href is not None:
                href, text = self.found[-1]
                self.found[-1] = (href, text + data)

        def handle_endtag(self, tag):
            if tag == "a":
                self.href = None

    parser = Anchors()
    parser.feed(html)
    return parser.found


def references_section(index: str) -> str:
    return index.split("<h2>References</h2>")[1].split("<h2>Answered")[0]


class _Base(StoreCase):
    def answer_all(self, item_id: str, count: int = 2) -> None:
        home = self.root / "home"
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        for part in range(1, count + 1):
            quarterdeck.send_answer(quarterdeck.store_path(), home,
                                    {"part": f"{item_id}.{part}", "answer": f"Answer {part}", "revision": ""})


class ResourceLinkTests(_Base):
    def add_review(self) -> str:
        return self.add("resource", "Design review", "A saved review.", "--url", "https://example.com/review",
                        "--link-text", "The review")

    def test_one_link_keeps_the_original_body_shape(self) -> None:
        self.add_review()
        self.assertEqual(self.store()["items"][0]["body"], {"url": "https://example.com/review", "link_text": "The review"})

    def test_several_links_are_saved_in_order_and_shown_everywhere(self) -> None:
        self.add("resource", "Design review", "A saved review.", "--url", "https://example.com/a", "--link-text", "First",
                 "--url", "https://example.com/b", "--link-text", "Second")
        self.assertEqual(self.store()["items"][0]["body"]["links"],
                         [{"text": "First", "url": "https://example.com/a"}, {"text": "Second", "url": "https://example.com/b"}])
        code, shown, err = self.run_cli("show", "Q1")
        self.assertEqual(code, 0, err)
        self.assertIn("First: https://example.com/a\nSecond: https://example.com/b", shown)
        pages = quarterdeck.site_pages(quarterdeck.read_store(quarterdeck.store_path()), "Quarterdeck", "index.html")
        expected = [("https://example.com/a", "First"), ("https://example.com/b", "Second")]
        item_links = [pair for pair in anchors(pages["q1.html"]) if pair[0].startswith("https://example.com/")]
        self.assertEqual(item_links, expected)
        row_links = [pair for pair in anchors(references_section(pages["index.html"])) if pair[0].startswith("https://example.com/")]
        self.assertEqual(row_links, [("https://example.com/a", "Design review"), ("https://example.com/b", "Second")])

    def test_many_links_are_accepted(self) -> None:
        pairs = [arg for n in range(13) for arg in ("--link-text", f"Link {n}", "--url", f"https://example.com/{n}")]
        self.add("resource", "Many", "Many links.", *pairs)
        self.assertEqual(len(self.store()["items"][0]["body"]["links"]), 13)

    def test_a_list_reference_links_its_page_once(self) -> None:
        self.add("list", "Harbor chores", "Chores.", "--body-file", "-", stdin="Buy rope\n")
        pages = quarterdeck.site_pages(quarterdeck.read_store(quarterdeck.store_path()), "Quarterdeck", "index.html")
        row = references_section(pages["index.html"])
        self.assertEqual([href for href, _text in anchors(row)], ["q1.html"])
        self.assertIn("Q1", row)

    def test_unpaired_or_missing_links_are_refused(self) -> None:
        for extra in (("--url", "https://example.com/a", "--url", "https://example.com/b", "--link-text", "One"),
                      ("--link-text", "One")):
            code, _out, err = self.run_cli("add", "--kind", "resource", "--title", "T", "--description", "D", *extra)
            self.assertEqual(code, 2)
            self.assertIn("--link-text", err)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_edit_changes_only_title_description_and_links(self) -> None:
        self.add_review()
        before = self.store()["items"][0]
        code, out, err = self.run_cli("edit", "Q1", "--title", "Design review 2", "--description", "Now with the board",
                                      "--link-text", "The review", "--url", "https://example.com/review",
                                      "--link-text", "Review board", "--url", "https://example.com/board")
        self.assertEqual((code, out.strip()), (0, "Edited Q1"), err)
        after = self.store()
        item = after["items"][0]
        self.assertEqual((item["id"], item["saved_at"], item["kind"]), (before["id"], before["saved_at"], before["kind"]))
        self.assertEqual((item["title"], item["description"]), ("Design review 2", "Now with the board"))
        self.assertEqual(item["body"]["links"], [{"text": "The review", "url": "https://example.com/review"},
                                                 {"text": "Review board", "url": "https://example.com/board"}])
        self.assertEqual(after["next_id"], 2)
        self.assertEqual(self.run_cli("render", "--output", str(self.root / "out" / "index.html"))[0], 0)

    def test_edit_keeps_answers_and_messages(self) -> None:
        self.add("questions", "Decisions", "Open questions.", "--body-file", "-",
                 stdin=json.dumps([{"question": "Go?", "context": "Now", "options": ["Yes", "No"]}]))
        self.answer_all("Q1", 1)
        data = self.store()
        code, _out, err = self.run_cli("edit", "Q1", "--title", "Renamed")
        self.assertEqual(code, 0, err)
        item = self.store()["items"][0]
        self.assertEqual(item["title"], "Renamed")
        self.assertEqual(item["answers"], data["items"][0]["answers"])
        self.assertEqual(item["body"], data["items"][0]["body"])

    def test_edit_refusals_change_nothing(self) -> None:
        self.add_review()
        self.add("list", "Chores", "Chores.", "--body-file", "-", stdin="Buy rope\n")
        self.assertEqual(self.run_cli("converse", "--title", "Tide work", "--text", "Started.")[:2], (0, "Q3\n"))
        saved = quarterdeck.store_path().read_text()
        for item_id, kind in (("Q2", "list"), ("Q3", "conversation")):
            code, _out, err = self.run_cli("edit", item_id, "--title", "Renamed")
            self.assertEqual(code, 2)
            self.assertIn(f"{item_id} is a {kind}", err)
        cases = (("Q1",), ("Q9", "--title", "x"), ("Q1", "--title", " "), ("Q1", "--url", "https://example.com/x"),
                 ("Q1", "--link-text", "x", "--url", "javascript:alert(1)"), ("Q2", "--link-text", "x", "--url", "https://example.com/x"))
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli("edit", *argv)[0], 2)
                self.assertEqual(quarterdeck.store_path().read_text(), saved)


if __name__ == "__main__":
    unittest.main()
