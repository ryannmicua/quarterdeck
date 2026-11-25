from __future__ import annotations

import errno
import fcntl
import hashlib
import http.client
import io
import json
import os
import re
import select
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from contextlib import contextmanager, redirect_stderr, redirect_stdout, suppress
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import quarterdeck  # noqa: E402
import privacy_guard  # noqa: E402

QUESTIONS = [
    {"question": "Merge the tide tables now?", "context": "Checks are green and the preview looks right.",
     "options": ["Merge", "Hold"], "suggestion": "Merge"},
    {"question": "Which colour for the quay?", "context": "Red matches the old signs; blue matches the new boats.",
     "options": ["Red", "Blue"]},
]


class StoreCase(unittest.TestCase):
    """Each test gets its own state and config directories."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        patcher = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state"),
                                          "XDG_CONFIG_HOME": str(self.root / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch.object(sys, "stdin", io.StringIO(stdin)):
            code = quarterdeck.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    @contextmanager
    def serve_cli(self, *args: str):
        for _ in range(3):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            with subprocess.Popen(
                [sys.executable, str(ROOT / "quarterdeck.py"), "serve", "--host", "127.0.0.1",
                 "--port", str(port), *args],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ) as process:
                try:
                    self.assertTrue(select.select([process.stdout], [], [], 10)[0], "server did not start")
                    ready = process.stdout.readline()
                    if ready.startswith("Serving Quarterdeck"):
                        yield port
                        return
                    output, error = process.communicate(timeout=10)
                    if f"[Errno {errno.EADDRINUSE}]" not in error:
                        self.fail(f"server did not start: {ready}{output}{error}")
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=10)
        self.fail(f"server could not bind after three attempts: {error}")

    def add(self, kind: str, title: str, description: str = "Saved to come back to.", *extra: str, stdin: str = "") -> str:
        code, out, err = self.run_cli("add", "--kind", kind, "--title", title, "--description", description, *extra, stdin=stdin)
        self.assertEqual(code, 0, err)
        return out.strip()

    def add_all_kinds(self) -> list[str]:
        return [
            self.add("resource", "Calls redesign", "The Lavish page with the new Calls layout.",
                     "--url", "https://example.com/lavish/calls", "--link-text", "Calls redesign (Lavish)"),
            self.add("list", "Harbor chores", "Chores for the weekend.", "--body-file", "-",
                     stdin="[Urgent] Buy rope\n\n- Paint the hull\n3. Check the sails\n"),
            self.add("questions", "Tide launch decisions", "Two open questions, saved to decide later.",
                     "--body-file", "-", stdin=json.dumps(QUESTIONS)),
        ]

    def store(self) -> dict:
        return json.loads(quarterdeck.store_path().read_text(encoding="utf-8"))


class ItemCommandTests(StoreCase):
    def test_add_prints_sequential_ids_and_stores_each_kind_by_value(self) -> None:
        body = self.root / "chores.txt"
        body.write_text("Buy rope\nPaint the hull\n", encoding="utf-8")
        ids = self.add_all_kinds()
        ids.append(self.add("list", "From a file", "A list read from a file.", "--body-file", str(body)))
        self.assertEqual(ids, ["Q1", "Q2", "Q3", "Q4"])
        body.write_text("Changed later\n", encoding="utf-8")
        items = {item["id"]: item for item in self.store()["items"]}
        self.assertEqual(items["Q1"]["body"], {"url": "https://example.com/lavish/calls", "link_text": "Calls redesign (Lavish)"})
        self.assertEqual(items["Q2"]["body"]["entries"], ["[Urgent] Buy rope", "- Paint the hull", "3. Check the sails"])
        self.assertEqual(items["Q3"]["body"]["questions"][1]["options"], ["Red", "Blue"])
        self.assertEqual(items["Q4"]["body"]["entries"], ["Buy rope", "Paint the hull"], "the body is a copy, not a link")
        self.assertTrue(items["Q1"]["saved_at"].endswith("Z"))

    def test_removed_ids_are_never_reused(self) -> None:
        self.add_all_kinds()
        self.assertEqual(self.run_cli("remove", "Q2")[0], 0)
        self.assertEqual(self.run_cli("remove", "q3")[0], 0)
        self.assertEqual(self.add("list", "Later", "A later list.", "--body-file", "-", stdin="text"), "Q4")
        code, _out, err = self.run_cli("show", "Q2")
        self.assertEqual((code, err.strip()), (2, "quarterdeck: no item Q2"))

    def test_show_numbers_list_entries_and_questions(self) -> None:
        self.add_all_kinds()
        code, out, _err = self.run_cli("show", "Q3")
        self.assertEqual(code, 0)
        self.assertIn("Q3 · Tide launch decisions", out)
        self.assertIn("Q3.1  Merge the tide tables now?", out)
        self.assertIn("      Options: Merge | Hold", out)
        self.assertIn("      Suggestion: Merge", out)
        self.assertIn("Q3.2  Which colour for the quay?", out)
        self.assertIn("Q2.3  3. Check the sails", self.run_cli("show", "q2")[1])
        self.assertIn("Calls redesign (Lavish): https://example.com/lavish/calls", self.run_cli("show", "Q1")[1])

    def test_list_reports_saved_items_newest_first(self) -> None:
        self.add_all_kinds()
        code, listed, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertEqual([line.split()[0] for line in listed.splitlines()], ["Q3", "Q2", "Q1"])
        self.assertIn("Harbor chores", listed)

    def test_invalid_items_are_refused_and_nothing_is_saved(self) -> None:
        cases = [
            (("--kind", "resource", "--title", "T", "--description", "D", "--url", "https://example.com"), "", "--link-text"),
            (("--kind", "resource", "--title", "T", "--description", "D", "--url", "javascript:alert(1)", "--link-text", "L"), "",
             "http:// or https://"),
            (("--kind", "list", "--title", "T", "--description", "D", "--body-file", "-"), "\n\n", "at least one entry"),
            (("--kind", "list", "--title", "T", "--description", "D"), "", "needs a body"),
            (("--kind", "list", "--title", " ", "--description", "D", "--body-file", "-"), "text", "title must not be empty"),
            (("--kind", "questions", "--title", "T", "--description", "D", "--body-file", "-"),
             json.dumps([{"question": "Q?", "options": ["A"]}]), "context"),
            (("--kind", "questions", "--title", "T", "--description", "D", "--body-file", "-"),
             json.dumps([{"question": "Q?", "context": "C"}]), "options or a suggestion"),
            (("--kind", "questions", "--title", "T", "--description", "D", "--body-file", "-"), "not json", "not valid JSON"),
            (("--kind", "list", "--title", "T", "--description", "D", "--url", "https://example.com", "--body-file", "-"), "x",
             "only for a resource"),
        ]
        for url in ("https://example.com:notaport", "https://example.com:65536", "https://:443", "https://ex%20ample.com",
                    "https://[::1]bad"):
            cases.append((("--kind", "resource", "--title", "T", "--description", "D",
                           "--url", url, "--link-text", "L"), "", "valid hostname and port"))
        for argv, stdin, message in cases:
            with self.subTest(message=message):
                code, _out, err = self.run_cli("add", *argv, stdin=stdin)
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_store_is_private_and_a_damaged_store_is_reported(self) -> None:
        self.add_all_kinds()
        path = quarterdeck.store_path()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        path.write_text("{not json", encoding="utf-8")
        code, _out, err = self.run_cli("list")
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)

    def test_damaged_record_is_rejected_before_any_command_consumes_it(self) -> None:
        self.add_all_kinds()
        path = quarterdeck.store_path()
        saved = path.read_text()
        output = self.root / "out" / "index.html"
        cases = (
            (0, "id", "q1"), (0, "id", "Q01"), (0, "id", "Q5"), (0, "title", ""),
            (0, "description", 3), (0, "saved_at", "2026-02-30T12:00:00Z"), (0, "body", None),
            (0, "body", {"url": "https://example.com:no", "link_text": "Resource"}),
            (0, "body", {"url": "https://example.com", "link_text": None}),
            (1, "body", {"entries": []}), (1, "body", {"entries": [1]}),
            (2, "body", {"questions": [{"question": "Pick?", "options": ["A"]}]}),
            (2, "body", {"questions": [{"question": "Pick?", "context": "Now", "options": [1]}]}),
            (2, "body", {"questions": [{"question": "Pick?", "context": "Now", "options": [], "suggestion": 1}]}),
        )
        for index, field, value in cases:
            with self.subTest(index=index, field=field, value=value):
                damaged = json.loads(saved)
                damaged["items"][index][field] = value
                snapshot = json.dumps(damaged)
                path.write_text(snapshot)
                for args in (("list",), ("show", "Q1"), ("remove", "Q1"), ("render", "--output", str(output)),
                             ("add", "--kind", "list", "--title", "Fresh", "--description", "Saved", "--body-file", "-")):
                    self.assertEqual(self.run_cli(*args, stdin="Buy rope")[0], 2, args)
                self.assertEqual(path.read_text(), snapshot)
                self.assertFalse(output.exists())

        for version in (True, 1.0):
            damaged = json.loads(saved)
            damaged["version"] = version
            path.write_text(json.dumps(damaged))
            self.assertEqual(self.run_cli("list")[0], 2)

    def test_show_and_remove_require_a_canonical_id_in_either_case(self) -> None:
        self.add_all_kinds()
        for command in ("show", "remove"):
            for value in ("1", "Q01", " Q1 ", "Q0", "Q1.1", "Q１"):
                with self.subTest(command=command, value=value):
                    self.assertEqual(self.run_cli(command, value)[0], 2)
        self.assertEqual(self.run_cli("show", "Q1")[0], 0)
        self.assertEqual(self.run_cli("show", "q1")[0], 0)
        self.assertEqual(self.run_cli("remove", "q1")[0], 0)

    def test_concurrent_adds_get_distinct_ids(self) -> None:
        def add(number: int) -> str:
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "add", "--kind", "list", "--title", f"List {number}",
                 "--description", "A fictional list", "--body-file", "-"],
                input=f"Entry {number}\n", capture_output=True, text=True, check=True, timeout=10)
            return result.stdout.strip()

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(add, range(8)))
        expected = [f"Q{number}" for number in range(1, 9)]
        self.assertCountEqual(ids, expected)
        self.assertEqual([line.split()[0] for line in self.run_cli("list")[1].splitlines()], expected[::-1])
        for number, item_id in enumerate(ids):
            code, shown, err = self.run_cli("show", item_id)
            self.assertEqual(code, 0, err)
            self.assertIn(f"{item_id}.1  Entry {number}", shown)


class PageTests(StoreCase):
    def pages(self) -> dict[str, str]:
        return quarterdeck.site_pages(quarterdeck.read_store(quarterdeck.store_path()), "Quarterdeck", "index.html")

    def assert_no_library_controls(self, page: str) -> None:
        class Controls(HTMLParser):
            def handle_starttag(parser, tag, attrs):
                attributes = dict(attrs)
                self.assertNotEqual(tag, "dialog")
                self.assertNotEqual(attributes.get("id"), "open-library")
                if tag == "input":
                    self.assertNotEqual(attributes.get("type"), "search")

        parser = Controls()
        parser.feed(page)
        parser.close()

    def test_empty_quarterdeck_shows_a_plain_empty_state(self) -> None:
        pages = self.pages()
        self.assertEqual(set(pages), {"index.html"})
        self.assertIn("Nothing is saved yet.", pages["index.html"])
        self.assertNotIn("<table", pages["index.html"])

    def served_pages(self) -> tuple[str, dict]:
        data = quarterdeck.read_store(quarterdeck.store_path())
        return quarterdeck.list_page(data, "Quarterdeck", served=True), data

    def inbox_home(self) -> Path:
        home = self.root / "home"
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        return home

    def answer_all(self, item_id: str, count: int = 2) -> None:
        home = self.inbox_home()
        for part in range(1, count + 1):
            quarterdeck.send_answer(quarterdeck.store_path(), home,
                                    {"part": f"{item_id}.{part}", "answer": f"Answer {part}", "revision": ""})

    def test_saved_page_lists_items_newest_first_with_their_fields(self) -> None:
        self.add_all_kinds()
        index = self.pages()["index.html"]
        for gone in ("My desk", "Needs attention", "Reviewed questions", "<table"):
            self.assertNotIn(gone, index)
        self.assertLess(index.index("<h1>Desk</h1>"), index.index("<h2>References</h2>"))
        self.assertLess(index.index(">Q2<"), index.index(">Q1<"))
        self.assertIn("Harbor chores", index)
        self.assertIn('<h3>Tide launch decisions</h3>', index)
        self.assertIn('href="q3.html#Q3.1" aria-label="Read questions for Tide launch decisions">Read questions</a>', index)
        self.assertNotIn('href="q3.html"', index)
        self.assertEqual(index.count('href="q3.html#Q3.1"'), 1)
        self.assertIn("Two open questions, saved to decide later.", index)
        self.assertIn("<li>Merge the tide tables now?</li><li>Which colour for the quay?</li>", index)
        self.assert_no_library_controls(index)
        self.assertIn("<h2>Answered</h2>", index)
        self.assertIn("Nothing is answered yet.", index)

    def test_served_desk_opens_references_and_answered_in_one_pop_out(self) -> None:
        self.add_all_kinds()
        page, _ = self.served_pages()
        self.assertEqual(page.count('id="open-library"'), 1)
        self.assertEqual(page.count("<dialog"), 1)
        for text in ('role="tab" id="tab-references"', 'role="tab" id="tab-answered"', ">Close library<",
                     'id="search-references"', 'id="search-answered"'):
            self.assertIn(text, page)
        self.assertIn("Nothing is answered yet.", page.split('id="panel-answered"')[1])
        self.assertNotIn("<table", page)
        self.assertNotIn("<h2>References</h2>", page)
        self.assertNotIn("<h2>Answered</h2>", page)
        references = page.split('id="panel-references"')[1].split('id="panel-answered"')[0]
        self.assertLess(references.index('data-id="Q2"'), references.index('data-id="Q1"'))
        self.assertIn('href="q1.html">Calls redesign</a>', references)
        self.assertNotIn('href="https://example.com/lavish/calls"', references)

    def test_a_question_item_moves_between_desk_and_answered_by_itself(self) -> None:
        self.add_all_kinds()
        self.add("questions", "Pier lights", "Pick the lamp colour.", "--body-file", "-", stdin=json.dumps(QUESTIONS))
        for state in ("answered", "closed"):
            with self.subTest(state=state):
                self.assertEqual(self.run_cli("mark", "Q4", state)[0], 0)
                page, _ = self.served_pages()
                desk, library = page.split("<dialog", 1)
                self.assertNotIn("Pier lights", desk)
                self.assertIn('data-id="Q4"', library.split('id="panel-answered"')[1])
                self.assertIn("Tide launch decisions", desk)
        self.assertEqual(self.run_cli("mark", "Q4.2", "open")[0], 0)
        page, data = self.served_pages()
        desk, library = page.split("<dialog", 1)
        self.assertIn("Pier lights", desk)
        self.assertNotIn('data-id="Q4"', library)
        self.assertEqual([item["id"] for item in data["items"]], ["Q1", "Q2", "Q3", "Q4"])

    def test_answering_every_question_moves_the_item_and_a_pending_delivery_keeps_it_on_the_desk(self) -> None:
        self.add("questions", "Pier lights", "Pick the lamp colour.", "--body-file", "-", stdin=json.dumps(QUESTIONS))
        quarterdeck.send_answer(quarterdeck.store_path(), None, {"part": "Q1.1", "answer": "Merge", "revision": ""})
        quarterdeck.send_answer(quarterdeck.store_path(), None, {"part": "Q1.2", "answer": "Blue", "revision": ""})
        page, _ = self.served_pages()
        self.assertIn("Not delivered", page.split("<dialog", 1)[0])
        self.assertIn('href="q1.html" aria-label="Review answers for Pier lights">Review answers</a>', page)
        for part in (1, 2):
            record = quarterdeck.read_store(quarterdeck.store_path())["items"][0]["answers"][str(part)]
            quarterdeck.send_answer(quarterdeck.store_path(), self.inbox_home(),
                                    {"part": f"Q1.{part}", "answer": record["answer"], "revision": record["revision"]})
        page, _ = self.served_pages()
        desk, library = page.split("<dialog", 1)
        self.assertNotIn("Pier lights", desk)
        self.assertIn("Nothing is on your Desk.", desk)
        answered = library.split('id="panel-answered"')[1]
        self.assertIn('href="q1.html">Pier lights</a></div><p class="meta">Answered</p></li>', answered)
        self.assertNotIn('>Which colour for the quay?', answered)
        self.assertNotIn('>Blue<', answered)
        self.assertEqual(quarterdeck.read_store(quarterdeck.store_path())["items"][0]["answers"]["2"]["answer"], "Blue")

    def test_answered_rows_keep_newest_saved_items_first(self) -> None:
        for number in range(3):
            self.add("questions", f"Set {number}", "Pick.", "--body-file", "-", stdin=json.dumps(QUESTIONS[:1]))
        with patch.object(quarterdeck, "now_text", side_effect=["2026-03-01T10:00:00Z", "2026-05-01T10:00:00Z", "2026-04-01T10:00:00Z"] * 2):
            for number in (1, 2, 3):
                self.answer_all(f"Q{number}", 1)
        page, _ = self.served_pages()
        answered = page.split('id="panel-answered"')[1]
        self.assertEqual(re.findall(r'<a class="lib-title" href="(q\d.html)"', answered), ["q3.html", "q2.html", "q1.html"])
        self.assertNotIn("<time", answered)
        static_answered = self.pages()["index.html"].split("<h2>Answered</h2>")[1]
        self.assertEqual(re.findall(r'<a class="lib-title" href="(q\d.html)"', static_answered), ["q3.html", "q2.html", "q1.html"])

    def test_static_pages_keep_references_and_answered_as_plain_sections(self) -> None:
        self.add_all_kinds()
        self.add("questions", "Pier lights", "Pick the lamp colour.", "--body-file", "-", stdin=json.dumps(QUESTIONS[:1]))
        with patch.object(quarterdeck, "now_text", return_value="2026-05-01T10:00:00Z"):
            self.answer_all("Q4", 1)
        pages = self.pages()
        index = pages["index.html"]
        self.assertIn('<section class="page-section"><h2>References</h2>', index)
        self.assertIn('<section class="page-section"><h2>Answered</h2>', index)
        self.assertLess(index.index("<h2>References</h2>"), index.index("<h2>Answered</h2>"))
        self.assertIn('href="q1.html">Calls redesign</a>', index.split("<h2>References</h2>")[1].split("</section>")[0])
        answered = index.split("<h2>Answered</h2>")[1].split("</section>")[0]
        self.assertEqual(answered, '<ul class="lib-list"><li class="lib-item">'
                         '<a class="lib-title" href="q4.html">Pier lights</a></li></ul>')
        self.assertIn("Answer 1", pages["q4.html"])
        for page in pages.values():
            self.assert_no_library_controls(page)

    def test_item_page_lists_open_questions_first_and_folds_answered_ones(self) -> None:
        self.add("questions", "Pier lights", "Pick the lamp colour.", "--body-file", "-", stdin=json.dumps(QUESTIONS))
        self.answer_all("Q1", 1)
        data = quarterdeck.read_store(quarterdeck.store_path())
        for served in (True, False):
            with self.subTest(served=served):
                page = quarterdeck.item_page(data["items"][0], "Quarterdeck", served=served)
                body = page.split('<div id="questions">')[1].split("</main>")[0]
                self.assertLess(body.index('id="Q1.2"'), body.index('<details class="done">'))
                self.assertIn('<summary><span class="id">Q1.1</span><span class="sum-q">Merge the tide tables now?</span>'
                              '<span class="sum-a">Answer 1</span></summary>', body)
                self.assertIn("1 of 2 open", page)
                self.assertEqual(page.count('class="sendbar"'), 1 if served else 0)
                if served:
                    self.assertGreater(body.index('class="sendbar"'), body.index("</details>"))
                self.assertNotIn("Return to Desk</a>", page)
        for state in ("closed", "open", "answered"):
            with self.subTest(state=state):
                if state == "answered":
                    revision = self.store()["items"][0]["answers"]["1"]["revision"]
                    quarterdeck.send_answer(quarterdeck.store_path(), None,
                                            {"part": "Q1.1", "answer": "Correction", "revision": revision})
                else:
                    self.assertEqual(self.run_cli("mark", "Q1.1", state)[0], 0)
                data = quarterdeck.read_store(quarterdeck.store_path())
                for served in (True, False):
                    page = quarterdeck.item_page(data["items"][0], "Quarterdeck", served=served)
                    self.assertNotIn('<details class="done">', page)
                    self.assertLess(page.index('id="Q1.1"'), page.index('id="Q1.2"'))
                    self.assertIn("Not delivered" if state == "answered" else "Sent", page)

    def test_web_addresses_in_descriptions_become_links(self) -> None:
        self.add("resource", "Notes", "See https://example.com/notes/1, then <b> https://example.org/a?x=1&y=2 </b>. Not javascript:alert(1).",
                 "--url", "https://example.com/n", "--link-text", "Notes")
        pages = self.pages()
        for name in ("q1.html", "index.html"):
            self.assertIn('See <a href="https://example.com/notes/1" target="_blank" rel="noopener noreferrer">'
                          'https://example.com/notes/1</a>, then &lt;b&gt;', pages[name])
            self.assertIn('<a href="https://example.org/a?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">',
                          pages[name])
            self.assertIn('</a> &lt;/b&gt;. Not javascript:alert(1).', pages[name])
            self.assertNotIn("<b>", pages[name])

    def test_description_links_keep_destinations_and_skip_invalid_addresses(self) -> None:
        description = (
            "See https://example.org/Mercury_(planet). "
            "Skip https://example.org:99999/?x=1&y=2. "
            "Try http://[2001:db8::1]! Or <HTTPS://example.org/?q=go!>. "
            "Compare https://example.org/Alzheimer's_disease. "
            'Use "https://example.org/?tag=red;". '
            "See (details: https://example.org/docs#setup)! "
            "Docs: 'https://example.org/docs',https://example.org/runbook. "
            "Compare <https://example.org/search?q=(> and https://example.org/search?q=bug)fix. "
            "Install from git+https://example.org/tools.git@v2."
        )
        self.add("resource", "Notes", description, "--url", "https://example.org/notes", "--link-text", "Notes")
        self.add("questions", "Decisions", description, "--body-file", "-", stdin=json.dumps(QUESTIONS))
        expected = (
            'See <a href="https://example.org/Mercury_(planet)" target="_blank" rel="noopener noreferrer">'
            'https://example.org/Mercury_(planet)</a>. '
            'Skip https://example.org:99999/?x=1&amp;y=2. '
            'Try <a href="http://[2001:db8::1]" target="_blank" rel="noopener noreferrer">'
            'http://[2001:db8::1]</a>! Or &lt;'
            '<a href="HTTPS://example.org/?q=go!" target="_blank" rel="noopener noreferrer">'
            'HTTPS://example.org/?q=go!</a>&gt;. '
            'Compare <a href="https://example.org/Alzheimer&#x27;s_disease" target="_blank" rel="noopener noreferrer">'
            'https://example.org/Alzheimer&#x27;s_disease</a>. '
            'Use &quot;<a href="https://example.org/?tag=red;" target="_blank" rel="noopener noreferrer">'
            'https://example.org/?tag=red;</a>&quot;. '
            'See (details: <a href="https://example.org/docs#setup" target="_blank" rel="noopener noreferrer">'
            'https://example.org/docs#setup</a>)! '
            'Docs: &#x27;<a href="https://example.org/docs" target="_blank" rel="noopener noreferrer">'
            'https://example.org/docs</a>&#x27;,<a href="https://example.org/runbook" '
            'target="_blank" rel="noopener noreferrer">'
            'https://example.org/runbook</a>. '
            'Compare &lt;<a href="https://example.org/search?q=(" target="_blank" rel="noopener noreferrer">'
            'https://example.org/search?q=(</a>&gt; and <a href="https://example.org/search?q=bug)fix" '
            'target="_blank" rel="noopener noreferrer">https://example.org/search?q=bug)fix</a>. '
            'Install from git+https://example.org/tools.git@v2.'
        )
        pages = self.pages()
        self.assertIn(f'<p class="lib-desc">{expected}</p>', pages["index.html"])
        self.assertIn(f'<h3>Decisions</h3><p>{expected}</p>', pages["index.html"])
        for name in ("q1.html", "q2.html"):
            self.assertIn(f'<p class="lead">{expected}</p>', pages[name])

    def test_item_pages_show_id_title_and_numbered_parts(self) -> None:
        self.add_all_kinds()
        pages = self.pages()
        self.assertIn('<p class="id">Q1</p><h1>Calls redesign</h1>', pages["q1.html"])
        self.assertIn('<a class="open-link" href="https://example.com/lavish/calls" rel="noopener noreferrer">'
                      "Calls redesign (Lavish)</a>", pages["q1.html"])
        self.assertIn('<li id="Q2.3"><span class="id">Q2.3</span><span>3. Check the sails</span></li>', pages["q2.html"])
        questions = pages["q3.html"]
        self.assertIn('<span class="id">Q3.1</span>', questions)
        self.assertIn("Merge the tide tables now?", questions)
        self.assertIn("Checks are green and the preview looks right.", questions)
        self.assertIn("<li>Hold</li>", questions)
        self.assertIn('<strong>Suggested answer</strong><br>Merge', questions)
        for attribute in ("data-revision", "data-state", "data-pending"):
            self.assertNotIn(f" {attribute}=", questions)
        self.assertNotIn(">https://example.com/lavish/calls<", pages["q1.html"])

    def test_text_is_escaped_and_no_server_path_appears(self) -> None:
        self.add("list", "<script>alert(1)</script>", "A & B", "--body-file", "-", stdin="<b>bold</b>")
        pages = self.pages()
        for page in pages.values():
            self.assertNotIn("<script>alert(1)</script>", page)
            self.assertNotIn(str(self.root), page)
            self.assertNotIn(str(Path.home()), page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", pages["q1.html"])


LINKED = [
    {"question": "How should the pop-up show up?", "context": "Three layouts are drawn on the review page.",
     "options": ["Centered", "Docked"], "suggestion": "Docked",
     "links": [{"text": "See the mock-ups", "url": "https://example.org/review/popup"},
               {"text": "Second view", "url": "https://example.org/review/two"}]},
    {"question": "Which colour?", "context": "Plain.", "options": ["Red"]},
]


class QuestionAddressTests(StoreCase):
    def test_web_addresses_in_question_text_become_links_but_answers_do_not_change(self) -> None:
        prompt_url, context_url, option_url, suggestion_url = (
            f"https://example.org/{name}" for name in ("ask", "why", "opt", "tip")
        )
        questions = [{"question": f"Does {prompt_url} look right?", "context": f"See {context_url}, then decide.",
                      "options": [f"Use {option_url}", "Hold"], "suggestion": f"Try {suggestion_url}"}]
        code, _out, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                       "--body-file", "-", stdin=json.dumps(questions))
        self.assertEqual(code, 0, err)
        data = quarterdeck.read_store(quarterdeck.store_path())
        pages = quarterdeck.site_pages(data, "Quarterdeck", "index.html")
        served = quarterdeck.item_page(data["items"][0], "Quarterdeck", served=True)
        prompt_link = f'<a href="{prompt_url}" target="_blank" rel="noopener noreferrer">{prompt_url}</a>'
        self.assertIn(prompt_link, pages["index.html"])
        for page in (pages["q1.html"], served):
            self.assertIn(prompt_link, page)
            for url in (context_url, option_url):
                self.assertIn(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>', page)
        suggestion_link = (f'<a href="{suggestion_url}" target="_blank" '
                           f'rel="noopener noreferrer">{suggestion_url}</a>')
        self.assertIn(f'<p><strong>Suggested answer</strong><br>Try {suggestion_link}</p>', pages["q1.html"])
        self.assertIn(f'data-choice="Try {suggestion_url}" data-suggested> '
                      f'Try {suggestion_link} <small>Suggested</small></label>', served)
        self.assertIn(f'data-choice="Use {option_url}"', served, "the answer value stays plain text")


class QuestionLinkTests(StoreCase):
    def save(self, questions: object) -> tuple[int, str, str]:
        return self.run_cli("add", "--kind", "questions", "--title", "Pop-up", "--description", "Layout choice.",
                            "--body-file", "-", stdin=json.dumps(questions))

    def test_links_are_stored_shown_by_show_and_rendered(self) -> None:
        self.assertEqual(self.save(LINKED)[0], 0)
        stored = self.store()["items"][0]["body"]["questions"]
        expected_links = LINKED[0]["links"]
        self.assertEqual(stored[0]["links"], expected_links)
        self.assertNotIn("links", stored[1])
        code, shown, err = self.run_cli("show", "Q1")
        self.assertEqual(code, 0, err)
        data = quarterdeck.read_store(quarterdeck.store_path())
        pages = quarterdeck.site_pages(data, "Quarterdeck", "index.html")
        served_page = quarterdeck.item_page(data["items"][0], "Quarterdeck", served=True)
        for link in expected_links:
            text, url = link["text"], link["url"]
            with self.subTest(text=text):
                self.assertIn(f"      Link: {text}: {url}", shown)
                anchor = (f'<a class="ref-link" href="{url}" target="_blank" '
                          f'rel="noopener noreferrer">{text}</a>')
                self.assertLess(pages["q1.html"].index(anchor), pages["q1.html"].index("<li>Centered</li>"))
                self.assertLess(served_page.index(anchor), served_page.index('<div class="choices">'))
        card_link = ('<a class="ref-link" href="https://example.org/review/popup" target="_blank" '
                     'rel="noopener noreferrer">Q1.1 See the mock-ups</a>')
        self.assertEqual(pages["index.html"].count(card_link), 1)
        self.assertNotIn("Second view", pages["index.html"])

    def test_card_preview_uses_first_open_question_with_links(self) -> None:
        later_question = {"question": "Where should the button go?", "context": "Review the placements.",
                          "options": ["Top", "Bottom"],
                          "links": [{"text": "See the placements", "url": "https://example.org/review/placements"}]}
        self.assertEqual(self.save([{**LINKED[1], "links": []}, LINKED[0], later_question])[0], 0)
        self.assertNotIn("links", self.store()["items"][0]["body"]["questions"][0])
        first = "Q1.2 See the mock-ups"
        later = "Q1.3 See the placements"
        cases = [("Q1.2", "open", first), ("Q1.2", "answered", later), ("Q1.2", "closed", later),
                 ("Q1.3", "closed", None), ("Q1.3", "open", later), ("Q1.2", "open", first)]
        for part, state, expected in cases:
            with self.subTest(part=part, state=state):
                self.assertEqual(self.run_cli("mark", part, state, *(["--via", "chat"] if state == "answered" else []))[0], 0)
                data = quarterdeck.read_store(quarterdeck.store_path())
                page = quarterdeck.site_pages(data, "Quarterdeck", "index.html")["index.html"]
                for label in (first, later):
                    self.assertEqual(page.count(f">{label}</a>"), int(label == expected))

    def test_bad_links_are_refused(self) -> None:
        base = {"question": "Q?", "context": "C", "options": ["A"]}
        cases = [
            ([{"text": "x", "url": "javascript:alert(1)"}], "link 1 url must be an http:// or https://"),
            ([{"text": "x", "url": "ftp://example.org/a"}], "link 1 url"),
            ([{"text": "x"}], "link 1 must be an object with text and url"),
            ([{"url": "https://example.org"}], "link 1 must be an object with text and url"),
            ([{"text": " ", "url": "https://example.org"}], "link 1 text must not be empty"),
            ("https://example.org", "links must be a list"),
            *((value, "links must be a list") for value in ({}, False, 0, None, "")),
        ]
        for links, message in cases:
            code, _out, err = self.save([{**base, "links": links}])
            self.assertEqual(code, 2, links)
            self.assertIn(message, err)
        self.assertFalse(quarterdeck.store_path().exists())


class RenderTests(StoreCase):
    def test_render_replaces_historical_output_and_retires_only_marked_item_pages(self) -> None:
        self.add_all_kinds()
        output = self.root / "site" / "index.html"
        output.parent.mkdir()
        historical = '<html><head><meta name="quarterdeck-' + 'generated" content="read-only review page"></head></html>'
        for name in ("index.html", "snapshot.html", "q9.html"):
            (output.parent / name).write_text(historical)
        unrelated = output.parent / "q8.html"
        unrelated.write_text("<html>my own page</html>")
        code, out, err = self.run_cli("render", "--output", str(output))
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), str(output.resolve()))
        self.assertIn('href="q3.html#Q3.1" aria-label="Read questions for Tide launch decisions">Read questions</a>',
                      output.read_text())
        self.assertIn('<section class="decision" id="Q3.1"', (output.parent / "q3.html").read_text())
        self.assertEqual({path.name for path in output.parent.iterdir()},
                         {"index.html", "q1.html", "q2.html", "q3.html", "q8.html", "snapshot.html"})
        self.assertEqual((output.parent / "snapshot.html").read_text(), historical)
        self.assertEqual(unrelated.read_text(), "<html>my own page</html>")
        self.assertEqual(self.run_cli("remove", "Q3")[0], 0)
        self.assertEqual(self.run_cli("render", "--output", str(output))[0], 0)
        self.assertFalse((output.parent / "q3.html").exists())

    def test_custom_saved_filename_is_linked_from_every_page(self) -> None:
        self.add_all_kinds()
        output = self.root / "site" / "Shelf #1.html"
        self.assertEqual(self.run_cli("render", "--output", str(output))[0], 0)
        for path in output.parent.iterdir():
            self.assertEqual(path.read_text().count('href="Shelf%20%231.html"'), 2)
            self.assertEqual(urljoin(path.as_uri(), "Shelf%20%231.html"), output.as_uri())
        self.assertFalse((output.parent / "index.html").exists())

    def test_store_named_saved_pages_can_be_updated_in_an_external_export(self) -> None:
        self.add_all_kinds()
        directory = self.root / "site"
        for name in ("items.json", "items.json.lock"):
            code, _, err = self.run_cli("render", "--output", str(directory / name))
            self.assertEqual(code, 0, err)
        self.add("list", "New export item", "Added between renders.", "--body-file", "-", stdin="Line one")
        for name in ("items.json", "items.json.lock"):
            with self.subTest(name=name):
                output = directory / name
                code, _, err = self.run_cli("render", "--output", str(output))
                self.assertEqual(code, 0, err)
                self.assertIn("New export item", output.read_text())

    def test_render_leaves_unrelated_site_data_and_lock_files_in_place(self) -> None:
        self.add_all_kinds()
        site = self.root / "site"
        site.mkdir()
        data_file = site / "items.json"
        lock_file = site / "items.json.lock"
        data_file.write_text('{"items": ["navigation entry"]}')
        lock_file.touch()
        for directory in (site, site / "images" / "preview"):
            with self.subTest(directory=directory):
                output = directory / "index.html"
                code, _, err = self.run_cli("render", "--output", str(output))
                self.assertEqual(code, 0, err)
                self.assertTrue(output.is_file())
        for path in (data_file, lock_file):
            code, _, err = self.run_cli("render", "--output", str(path))
            self.assertEqual(code, 2)
            self.assertIn("refusing to overwrite an unrelated file", err)
        self.assertEqual(data_file.read_text(), '{"items": ["navigation entry"]}')
        self.assertEqual(lock_file.read_bytes(), b"")

    def test_render_preflights_destinations_and_protects_store(self) -> None:
        self.add_all_kinds()
        directory = self.root / "site"
        directory.mkdir()
        output = directory / "index.html"
        collision = directory / "q2.html"
        collision.write_text("Mine")
        config = self.root / "config.json"
        config.write_text(json.dumps({"output_dir": str(directory)}))
        for args in (("--output", str(output)), ("--config", str(config)), ("--output", str(collision))):
            self.assertEqual(self.run_cli("render", *args)[0], 2)
            self.assertEqual(set(directory.iterdir()), {collision})
            self.assertEqual(collision.read_text(), "Mine")
        output.write_text("My index")
        self.assertEqual(self.run_cli("render", "--output", str(output))[0], 2)
        self.assertEqual(output.read_text(), "My index")
        path = quarterdeck.store_path()
        before = path.read_bytes()
        self.assertEqual(self.run_cli("render", "--output", str(path))[0], 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual({file.name for file in path.parent.iterdir()}, {"items.json", "items.json.lock"})

    def test_render_uses_the_configured_output_and_title(self) -> None:
        config = self.root / "config.json"
        config.write_text(json.dumps({"page_title": "Captain's shelf", "output_dir": str(self.root / "out"),
                                      "homes": [{"path": "/somewhere"}], "captain_waits": {}}))
        code, _out, err = self.run_cli("render", "--config", str(config))
        self.assertEqual(code, 0, err)
        self.assertIn("<title>Captain&#x27;s shelf</title>", (self.root / "out" / "index.html").read_text())

    def test_config_rejects_unknown_keys_and_the_checkout_as_output(self) -> None:
        config = self.root / "config.json"
        config.write_text(json.dumps({"theme": "dark"}))
        self.assertIn("unsupported config key(s): theme", self.run_cli("render", "--config", str(config))[2])
        self.assertIn("outside the Quarterdeck checkout", self.run_cli("render", "--output", str(ROOT / "page.html"))[2])


class ServeTests(StoreCase):
    def start(self, base_path: str = "", *, home: Path | None = None, workers_home: Path | None = None) -> int:
        handler = quarterdeck.make_handler("Quarterdeck", base_path, home=home, workers_home=workers_home)
        server = quarterdeck.make_server("127.0.0.1", 0, handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def fetch(self, port: int, path: str, method: str = "GET", host: str | None = None) -> tuple[int, str, dict]:
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
        if host:
            request.add_header("Host", host)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8"), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8"), dict(error.headers)

    def post_answer(self, port, part, answer, revision="", origin=None, forwarded_proto=None):
        base = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(base + "/qd/answers", method="POST",
            headers={"Content-Type": "application/json", "Origin": base if origin is None else origin},
            data=json.dumps({"part": part, "answer": answer, "revision": revision}).encode())
        if forwarded_proto is not None:
            request.add_header("X-Forwarded-Proto", forwarded_proto)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_answer_delivery_retry_and_corrections(self):
        self.add_all_kinds()
        home = self.root / "home"
        port = self.start("/qd", home=home)
        answer = "Green & blue\nNo varnish."
        status, record = self.post_answer(port, "Q3.1", answer)
        self.assertEqual((200, "pending"), (status, record["delivery"]))
        self.assertIn("Not delivered", self.fetch(port, "/qd/q3.html")[1])
        saved = self.store()["items"][2]["answers"]["1"]
        self.assertEqual(0, self.run_cli("mark", "Q3.1", "open")[0])
        before_retry = self.store()["items"][2]["answers"]["1"]
        self.assertIn("Not delivered", self.fetch(port, "/qd/")[1])
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True)
        script.write_text('#!/bin/sh\nset -eu\n'
                          'test "$#" = 6 && test "$1" = note && test "$2" = --request-id && '
                          'test "$4" = --json && test "$5" = -- || exit 2\n'
                          'test "$PWD" = "$FM_HOME"\n'
                          'printf "%s" "$6" >> "$3"\n')
        script.chmod(0o700)
        self.assertEqual(self.post_answer(port, "Q3.1", answer, record["revision"])[0], 409)
        status, retry = self.post_answer(port, "Q3.1", answer, before_retry["revision"])
        self.assertEqual((status, retry["state"], retry["delivery"]), (200, "open", "sent"))
        self.assertEqual(self.post_answer(port, "Q3.1", answer, before_retry["revision"]), (200, retry))
        self.assertEqual(self.store()["items"][2]["answers"]["1"], {**before_retry, "delivery": "sent"})
        self.assertEqual((home / saved["request_id"]).read_text(), "Q3.1 Green & blue\nNo varnish.")
        self.assertEqual(self.post_answer(port, "Q3.1", "Changed")[0], 409)
        status, correction = self.post_answer(port, "Q3.1", "Changed", retry["revision"])
        self.assertEqual((status, correction["delivery"]), (200, "sent"))
        self.assertEqual(self.post_answer(port, "Q3.1", "Changed", retry["revision"]), (200, correction))
        corrected = self.store()["items"][2]["answers"]["1"]
        self.assertNotEqual(saved["request_id"], corrected["request_id"])
        self.assertEqual((home / corrected["request_id"]).read_text(), "Q3.1 Changed")
        self.assertIn("Answer: Changed", self.run_cli("show", "Q3")[1])
        self.assertEqual(0, self.run_cli("mark", "Q3.1", "open")[0])
        reopened = self.store()["items"][2]["answers"]["1"]
        self.assertNotIn("updated_at", reopened)
        self.assertEqual(self.post_answer(port, "Q3.1", "Changed", correction["revision"])[0], 409)
        self.assertEqual(self.store()["items"][2]["answers"]["1"], reopened)
        self.assertEqual(0, self.run_cli("mark", "Q3", "closed")[0])
        self.assertEqual(self.post_answer(port, "Q3.1", "New", correction["revision"])[0], 409)
        index = self.fetch(port, "/qd/")[1]
        self.assertIn("Nothing is on your Desk", index)
        self.assertNotIn("Review answers", index)
        self.assertIn('data-id="Q3"', index.split('id="panel-answered"')[1])
        closed = self.fetch(port, "/qd/q3.html")[1]
        self.assertIn("<li>Hold</li>", closed)
        self.assertIn('<strong>Suggested answer</strong><br>Merge', closed)
        self.assertNotIn("<textarea", closed)
        self.assertEqual(0, self.run_cli("mark", "Q3.2", "open")[0])
        self.assertEqual(set(self.store()["items"][2]["answers"]["2"]), {"state", "revision"})
        desk = self.fetch(port, "/qd/")[1].split("<h1>Desk</h1>", 1)[1].split('<section id="workers">', 1)[0]
        self.assertIn("<li>Which colour for the quay?</li>", desk)
        self.assertNotIn("Merge the tide tables now?", desk)
        self.assertIn('<span class="badge">1 of 2 open</span>', desk)
        self.assertIn('href="q3.html#Q3.2" aria-label="Answer questions for Tide launch decisions">Answer questions</a>', desk)
        question_page = self.fetch(port, "/qd/q3.html")[1]
        self.assertIn('<section class="decision" id="Q3.2"', question_page)
        self.assertIn('<p class="progress">1 of 2 open</p>', question_page)
        self.assertIn("Open (1 of 2 open)", self.run_cli("show", "Q3")[1])

    def test_pending_delivery_does_not_take_the_primary_desk_action(self):
        self.add("questions", "Open decisions", "Two choices.", "--body-file", "-", stdin=json.dumps(QUESTIONS))
        delivery = [{"question": "Deliver the harbour chart?", "context": "The chart is ready.", "options": ["Yes"]}]
        self.add("questions", "Delivery to retry", "A choice already made.", "--body-file", "-", stdin=json.dumps(delivery))
        port = self.start("/qd")
        self.assertEqual(self.post_answer(port, "Q2.1", "Yes")[0], 200)
        desk = self.fetch(port, "/qd/")[1].split("<h1>Desk</h1>", 1)[1].split('<section id="workers">', 1)[0]
        self.assertIn('class="card-link button-link" href="q2.html"', desk)
        self.assertIn('class="card-link work-action" href="q1.html#Q1.1"', desk)
        self.assertIn("<li>Merge the tide tables now?</li><li>Which colour for the quay?</li>", desk)
        self.assertNotIn("Deliver the harbour chart?", desk)

    def test_invalid_answers_leave_the_store_untouched(self):
        self.add_all_kinds()
        port = self.start("/qd")
        before = self.store()
        for part, answer in (("Q3.3", "Hold"), ("Q4.1", "Hold"), ("Q1.1", "Hold"), ("Q3.1", " "),
                             ("Q3.1", "a" * 20001), ("Q3.1", "Hold\u0085until morning")):
            self.assertEqual(self.post_answer(port, part, answer)[0], 400)
        for origin in ("", "null", "https://example.org"):
            self.assertEqual(self.post_answer(port, "Q3.1", "Hold", origin=origin)[0], 403)
        self.assertEqual(self.store(), before)

    def test_answer_retries_after_either_store_read_fails(self):
        self.add_all_kinds()
        port = self.start("/qd", home=self.root / "home")
        path = quarterdeck.store_path()
        read_text = type(path).read_text
        for failed_read, part, answer in ((1, "Q3.1", "Hold"), (2, "Q3.2", "Blue")):
            with self.subTest(failed_read=failed_read):
                reads = 0

                def read_with_failure(file, *args, **kwargs):
                    nonlocal reads
                    if file == path:
                        reads += 1
                        if reads == failed_read:
                            raise PermissionError(f"could not read {file}")
                    return read_text(file, *args, **kwargs)

                with patch.object(type(path), "read_text", read_with_failure):
                    status, body = self.post_answer(port, part, answer)
                self.assertEqual((status, body), (500, b"Could not save the answer."))
                first_save = self.store()["items"][2].get("answers", {}).get(part[-1])
                if failed_read == 1:
                    self.assertIsNone(first_save)
                else:
                    self.assertEqual(first_save["answer"], answer)
                status, record = self.post_answer(port, part, answer)
                self.assertEqual((status, record["answer"]), (200, answer))
                if first_save:
                    self.assertEqual(record["revision"], first_save["revision"])

    def test_answer_without_delivery_work_survives_a_failing_second_read(self):
        self.add_all_kinds()
        path = quarterdeck.store_path()
        read_text = type(path).read_text
        home = self.root / "home"
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        bound = self.start("/qd", home=home)
        unbound = self.start("/qd")
        status, sent = self.post_answer(bound, "Q3.2", "Blue")
        self.assertEqual((status, sent["delivery"]), (200, "sent"))
        for port, part, answer, delivery in ((unbound, "Q3.1", "Hold", "pending"), (bound, "Q3.2", "Blue", "sent")):
            with self.subTest(delivery=delivery):
                reads = 0

                def read_once(file, *args, **kwargs):
                    nonlocal reads
                    if file == path:
                        reads += 1
                        if reads > 1:
                            raise PermissionError("store read failed")
                    return read_text(file, *args, **kwargs)

                with patch.object(type(path), "read_text", read_once):
                    status, record = self.post_answer(port, part, answer)
                self.assertEqual(status, 200)
                self.assertEqual((record["answer"], record["delivery"]), (answer, delivery))

    def test_removed_item_conflicts_before_save_and_before_delivery(self):
        self.add_all_kinds()
        home = self.root / "home"
        port = self.start("/qd", home=home)
        self.assertEqual(self.fetch(port, "/qd/q3.html")[0], 200)
        changing_store = quarterdeck.changing_store
        removed = []

        @contextmanager
        def remove_after_save(path):
            with changing_store(path) as data:
                yield data
            if not removed:
                with patch.object(quarterdeck, "changing_store", changing_store):
                    removed.append(self.run_cli("remove", "Q3")[0])

        with patch.object(quarterdeck, "changing_store", remove_after_save):
            self.assertEqual(self.post_answer(port, "Q3.1", "Hold"),
                             (409, b"Item removed. Reload before sending."))
        self.assertEqual(removed, [0])
        self.assertEqual(self.post_answer(port, "Q3.2", "Blue"), (409, b"Item removed. Reload before sending."))
        self.assertEqual(self.fetch(port, "/qd/q3.html")[0], 404)

    def test_answer_origin_through_tls_terminating_proxy(self):
        self.add_all_kinds()
        port = self.start("/qd")
        public_origin = f"https://127.0.0.1:{port}"
        before = self.store()
        for origin, forwarded in ((public_origin, None), ("https://example.org", "https"),
                                  (f"http://127.0.0.1:{port}", "https")):
            self.assertEqual(self.post_answer(port, "Q3.1", "Hold", origin=origin, forwarded_proto=forwarded)[0], 403)
        self.assertEqual(self.store(), before)
        status, record = self.post_answer(port, "Q3.1", "Hold", origin=public_origin, forwarded_proto="https")
        self.assertEqual((status, record["answer"]), (200, "Hold"))

    def check_answer_page(self, port, script, path="/qd/q1.html", *, viewport=None):
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the answer-card browser tests")
        status, page, _ = self.fetch(port, path)
        self.assertEqual(status, 200)
        fixture = self.root / "question.html"
        page = page.replace("</body>", """
<style>#browser-result { display:none }</style>
<output id="browser-result">Incomplete</output>
<script>
const result = (window.frameElement ? parent.document : document).getElementById('browser-result');
const check = (condition, message) => { if (!condition) throw new Error(message); };
const field = n => document.getElementById('answer-Q1.' + n);
const card = n => document.getElementById('Q1.' + n);
const button = id => document.getElementById(id);
const typeAnswer = (n, text) => {
  field(n).value = text;
  field(n).dispatchEvent(new Event('input', {bubbles: true}));
};
const sendBatch = () => new Promise(resolve => {
  const status = document.querySelector('[role="status"]');
  const observer = new MutationObserver(() => {
    if (!status.textContent.startsWith('Sending')) {
      observer.disconnect();
      resolve();
    }
  });
  observer.observe(status, {childList: true});
  button('send').click();
});
(async () => {
""" + script + """
  result.textContent = 'PASS';
})().catch(error => { result.textContent = error.message; });
</script>
</body>""")
        timing = []
        if viewport:
            width, height = viewport
            page = (f'<iframe width="{width}" height="{height}" style="border:0" srcdoc="{escape(page)}"></iframe>'
                    '<output id="browser-result">Incomplete</output>')
            timing = ["--virtual-time-budget=1000"]
        fixture.write_text(page, encoding="utf-8")
        result = subprocess.run([chrome, "--headless", "--disable-background-networking", "--no-first-run", "--lang=en-US",
                                 f"--user-data-dir={self.root / 'browser'}", *timing, "--dump-dom", fixture.as_uri()],
                                env={**os.environ, "TZ": "Asia/Tokyo", "LC_ALL": "en_US.utf-8"},
                                capture_output=True, text=True, timeout=30, check=True)
        browser_result = result.stdout.partition('<output id="browser-result">')[2].partition("</output>")[0]
        self.assertEqual(browser_result, "PASS", result.stderr)

    def test_keyboard_visual_viewport_repositions_send_and_library(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps(QUESTIONS * 3))
        self.add("resource", "Harbor chart", "Review the route.", "--url", "https://example.org/chart",
                 "--link-text", "Open chart")
        port = self.start("/qd")
        viewport_events = r"""
const adjust = (height, offsetTop, event) => {
  Object.defineProperties(visualViewport, {
    height: {value:height, configurable:true},
    offsetTop: {value:offsetTop, configurable:true}
  });
  visualViewport.dispatchEvent(new Event(event));
  check(innerHeight === 844, 'The keyboard fixture resized the layout viewport');
};
const near = (actual, expected) => Math.abs(actual - expected) < 1;
"""
        self.check_answer_page(port, viewport_events + r"""
const bar = button('send').closest('.sendbar');
field(6).focus();
adjust(240, 60, 'resize');
check(near(bar.getBoundingClientRect().bottom, 300), 'The Send bar stayed behind the keyboard');
adjust(240, 100, 'scroll');
check(near(bar.getBoundingClientRect().bottom, 340), 'The Send bar did not follow viewport panning');
scrollTo(0, document.documentElement.scrollHeight);
check(field(6).getBoundingClientRect().bottom <= bar.getBoundingClientRect().top,
      'The last field could not scroll clear of the keyboard and bar');
adjust(844, 0, 'resize');
check(near(bar.getBoundingClientRect().bottom, 844), 'The Send bar did not return after closing the keyboard');
check(near(parseFloat(getComputedStyle(document.querySelector('main')).paddingBottom), bar.getBoundingClientRect().height),
      'The page kept extra clearance after closing the keyboard');
""", viewport=(390, 844))
        for width in (390, 900):
            with self.subTest(width=width):
                self.check_answer_page(port, viewport_events + r"""
button('open-library').click();
const dialog = button('library');
for (const animation of dialog.getAnimations()) animation.finish();
const normalHeight = dialog.getBoundingClientRect().height;
const search = button('search-references');
search.focus();
adjust(60, 60, 'resize');
let bounds = dialog.getBoundingClientRect();
check(near(bounds.bottom, 120) && near(bounds.top, 60),
      'The library did not fit above the keyboard');
const searchBounds = search.getBoundingClientRect();
check(document.activeElement === search && searchBounds.top >= bounds.top && searchBounds.bottom <= bounds.bottom + 1,
      'The focused search was clipped when the keyboard opened');
search.value = 'harbor';
search.dispatchEvent(new Event('input', {bubbles:true}));
const resultLink = dialog.querySelector('a[href="q2.html"]');
resultLink.scrollIntoView({block:'center'});
bounds = dialog.getBoundingClientRect();
const linkBounds = resultLink.getBoundingClientRect();
check(linkBounds.top >= bounds.top && linkBounds.bottom <= bounds.bottom, 'The result could not scroll into view');
for (const control of dialog.querySelectorAll('[role="tab"], [data-close]')) {
  control.scrollIntoView({block:'center'});
  const controlBounds = control.getBoundingClientRect();
  check(controlBounds.top >= bounds.top - 1 && controlBounds.bottom <= bounds.bottom + 1,
        'The short library clipped ' + control.textContent);
}
adjust(60, 90, 'scroll');
check(near(dialog.getBoundingClientRect().bottom, 150), 'The library did not follow viewport panning');
adjust(844, 0, 'resize');
bounds = dialog.getBoundingClientRect();
check(near(bounds.bottom, 844) && near(bounds.height, normalHeight), 'The library did not return to its original size');
""", path="/qd/", viewport=(width, 844))

    def test_send_bar_stays_at_viewport_bottom_and_reserves_its_height(self):
        for count in (1, 12):
            self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                     stdin=json.dumps(QUESTIONS[:1] * count))
        port = self.start("/qd")
        for width, height in ((390, 844), (1440, 900)):
            for item, count in ((1, 1), (2, 12)):
                with self.subTest(viewport=(width, height), questions=count):
                    self.check_answer_page(port, f"""
check(innerWidth === {width} && innerHeight === {height}, 'Wrong fixture viewport');
check(document.querySelectorAll('textarea').length === {count}, 'Wrong fixture question count');
""" + r"""
const main = document.querySelector('main');
const bar = button('send').closest('.sendbar');
const lastField = [...document.querySelectorAll('textarea')].pop();
const checkPosition = () => {
  const bounds = bar.getBoundingClientRect();
  check(Math.abs(bounds.bottom - innerHeight) < 1, 'The send bar moved away from the viewport bottom');
  check(Math.abs(parseFloat(getComputedStyle(main).paddingBottom) - bounds.height) < 1,
        'The page did not reserve exactly one send bar height');
};
const checkPageEnd = () => {
  scrollTo(0, document.documentElement.scrollHeight);
  checkPosition();
  check(lastField.getBoundingClientRect().bottom <= bar.getBoundingClientRect().top, 'The bar covered the last field');
  if (document.documentElement.scrollHeight > innerHeight) {
    check(Math.abs(main.getBoundingClientRect().bottom - innerHeight) < 1, 'Extra space remained below the page end');
  }
};
checkPosition();
checkPageEnd();
const waitForBarResize = () => new Promise(resolve => {
  const observer = new ResizeObserver(() => { observer.disconnect(); resolve(); });
  observer.observe(bar);
});
const focusAboveBar = control => {
  control.focus({preventScroll:true});
  scrollBy(0, control.getBoundingClientRect().bottom - bar.getBoundingClientRect().top + 4);
  check(document.activeElement === control && control.getBoundingClientRect().bottom <= bar.getBoundingClientRect().top,
        'The focused control was not visible before the bar grew');
};
let statusFocus;
if (innerWidth === 390 && document.querySelectorAll('textarea').length > 1) {
  const question = document.querySelectorAll('.decision')[2];
  const choice = question.querySelector('[data-choice]');
  focusAboveBar(choice);
  choice.click();
  check(choice.getBoundingClientRect().bottom > bar.getBoundingClientRect().top, 'Undo did not grow the bar over the choice');
  await waitForBarResize();
  check(document.activeElement === choice && choice.getBoundingClientRect().bottom <= bar.getBoundingClientRect().top,
        'Showing Undo covered the focused choice');
  statusFocus = question.querySelector('textarea');
  focusAboveBar(statusFocus);
}
const originalHeight = bar.getBoundingClientRect().height;
bar.querySelector('output').textContent = 'An answer was rejected. '.repeat(12);
await waitForBarResize();
check(bar.getBoundingClientRect().height > originalHeight, 'The status fixture did not grow the bar');
if (statusFocus) {
  check(document.activeElement === statusFocus && statusFocus.getBoundingClientRect().bottom <= bar.getBoundingClientRect().top,
        'The status message covered the focused answer');
}
checkPageEnd();
""", path=f"/qd/q{item}.html", viewport=(width, height))

    def test_verbose_send_error_leaves_the_question_and_controls_reachable(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps(QUESTIONS[:1]))
        port = self.start("/qd")
        for height in (844, 140):
            with self.subTest(height=height):
                self.check_answer_page(port, r"""
const error = 'Gateway failure. '.repeat(1500) + 'End of response.';
window.fetch = async () => ({ok: false, status: 502, text: async () => error});
card(1).querySelector('[data-choice="Hold"]').click();
await sendBatch();
const bar = button('send').closest('.sendbar');
const status = bar.querySelector('output');
check(status.textContent.endsWith('End of response.') && status.scrollHeight > status.clientHeight,
      'A long error was lost or did not fit in its own scroll area');
status.focus();
status.scrollTop = status.scrollHeight;
check(document.activeElement === status && status.scrollTop > 0, 'The full error was not reachable');
check(bar.getBoundingClientRect().top >= innerHeight / 2, 'The error covered most of the question view');
check(!button('send').disabled && field(1).value === 'Hold', 'The error prevented retrying the draft');
for (const control of [button('undo'), status, button('send'), field(1)]) {
  control.scrollIntoView({block:'center'});
  const bounds = control.getBoundingClientRect();
  check(control.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)),
        'The short viewport hid ' + control.tagName + ' ' + control.id);
}
""", viewport=(390, height))

    def test_bulk_suggestions_skip_saved_answers_and_drafts(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps([QUESTIONS[0]] * 7))
        port = self.start("/qd")
        self.assertEqual(self.post_answer(port, "Q1.1", "Wait for low tide")[0], 200)
        self.assertEqual(self.post_answer(port, "Q1.6", "Ask the harbourmaster")[0], 200)
        for part, state in (("Q1.5", "answered"), ("Q1.6", "open"), ("Q1.7", "closed")):
            self.assertEqual(self.run_cli("mark", part, state, *(["--via", "chat"] if state == "answered" else []))[0], 0)
        self.check_answer_page(port, """
typeAnswer(2, 'Wait for the pilot');
card(3).querySelector('input[data-choice="Hold"]').click();
button('suggest').click();
check(field(1).value === 'Wait for low tide', 'Bulk suggestions overwrote a saved answer');
check(field(2).value === 'Wait for the pilot', 'Bulk suggestions overwrote a typed draft');
check(field(3).value === 'Hold', 'Bulk suggestions overwrote a chosen option');
check(field(4).value === 'Merge', 'Bulk suggestions skipped an untouched open question');
check(field(5).value === '', 'Bulk suggestions filled a manually answered question');
check(field(6).value === 'Ask the harbourmaster', 'Bulk suggestions overwrote a reopened answer');
check(field(7) === null, 'A closed question had an editable field');
check(button('suggest').disabled, 'Bulk suggestions remained available with nothing to fill');
""")

    def test_an_answered_question_folds_to_one_line_below_the_open_ones(self):
        review = [{"text": "Review the plan", "url": "https://example.org/review"}]
        question = {**QUESTIONS[0], "question": "Merge the tide tables now? " * 8, "links": review}
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps([question, {**QUESTIONS[1], "links": review}, question]))
        home = self.root / "home"
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        port = self.start("/qd", home=home)
        self.assertEqual(self.post_answer(port, "Q1.3", "Wait for the harbourmaster. " * 8)[1]["delivery"], "sent")
        self.check_answer_page(port, r"""
const list = document.querySelector('#questions');
const order = () => [...list.children].map(row => row.id || row.lastElementChild.id).join();
const checkSummary = summary => {
  const question = summary.querySelector('.sum-q');
  const answer = summary.querySelector('.sum-a');
  for (const width of [300, 650]) {
    list.style.maxWidth = width + 'px';
    check(question.offsetTop === answer.offsetTop && summary.scrollWidth === summary.clientWidth,
          'Summary text did not fit on one row at width ' + width);
    for (const text of [question, answer]) {
      const style = getComputedStyle(text);
      check(style.whiteSpace === 'nowrap' && style.textOverflow === 'ellipsis' &&
            style.overflowX === 'hidden' && text.scrollWidth > text.clientWidth,
            'Long summary text was not shortened with an ellipsis at width ' + width);
    }
  }
  list.style.maxWidth = '';
};
check(card(1).parentElement === list && card(2).parentElement === list, 'Open questions should not be folded');
const initiallyFolded = card(3).closest('details');
check(initiallyFolded && !initiallyFolded.open, 'A previously delivered question did not start folded');
checkSummary(initiallyFolded.querySelector('summary'));
let delivery = 'sent';
let focusDuringSend;
window.fetch = async (url, options) => {
  const {answer} = JSON.parse(options.body);
  if (focusDuringSend) {
    focusDuringSend.focus();
    check(document.activeElement === focusDuringSend, 'The focus fixture did not reach the row during delivery');
  }
  return {ok: true, json: async () => ({answer, state: 'answered', revision: 'saved-revision',
    answered_at: '2026-01-12T15:00:00Z', delivery})};
};
typeAnswer(2, 'Blue');
focusDuringSend = card(2).querySelector('a');
await sendBatch();
const secondSummary = card(2).closest('details').querySelector('summary');
check(document.activeElement === secondSummary, 'First delivery dropped focus from the row');
focusDuringSend = null;
check(order() === 'Q1.1,Q1.2,Q1.3', 'The later question was not inserted before a higher-numbered folded question');
check(document.querySelector('.progress').textContent === '1 of 3 open', 'Progress was stale');
const longAnswer = 'Wait for confirmation from the harbourmaster. '.repeat(8).trim();
typeAnswer(1, longAnswer);
delivery = 'pending';
await sendBatch();
check(list.children[0] === card(1) && card(1).querySelector('.delivery').textContent === 'Not delivered',
      'The pending answer moved or hid its delivery status');
delivery = 'sent';
await sendBatch();
check(order() === 'Q1.1,Q1.2,Q1.3', 'Delivery order changed the order of the folded questions');
const folded = list.children[0];
check(folded.matches('details.done') && !folded.open, 'The answered question was not folded');
const summary = folded.querySelector('summary');
checkSummary(summary);
summary.click();
check(folded.open && field(1).value === longAnswer &&
      card(1).querySelector('h2').textContent === 'Merge the tide tables now? '.repeat(8).trim(),
      'Opening the folded row did not reveal the full question and answer');
typeAnswer(1, 'Hold for the pilot');
focusDuringSend = card(1).querySelector('a');
await sendBatch();
check(!folded.open && folded.querySelector('.sum-a').textContent === 'Hold for the pilot',
      'The delivered correction did not close the row with the corrected answer');
check(document.activeElement === summary, 'Refolding a correction dropped focus from the row');
summary.click();
typeAnswer(1, 'Use the next tide');
delivery = 'pending';
focusDuringSend = summary;
await sendBatch();
check(order() === 'Q1.1,Q1.2,Q1.3' && card(1).parentElement === list,
      'The pending correction did not return to its place among expanded questions');
check(card(1).querySelector('.delivery').textContent === 'Not delivered', 'The pending correction lost its delivery status');
check(document.activeElement === card(1).querySelector('a'), 'Expanding a pending correction lost focus from its removed summary');
secondSummary.click();
typeAnswer(2, 'Red');
focusDuringSend = card(2).querySelector('a');
await sendBatch();
check(document.activeElement === focusDuringSend, 'Expanding a pending correction dropped focus from its existing link');
""")

    def test_narrow_library_header_keeps_both_tabs_and_close_visible(self):
        self.check_answer_page(self.start("/qd"), r"""
check(innerWidth === 320, 'Wrong fixture viewport');
button('open-library').click();
const dialog = button('library');
const tabs = [...dialog.querySelectorAll('[role="tab"]')];
const close = dialog.querySelector('button[data-close]');
const sheet = dialog.getBoundingClientRect();
for (const control of [...tabs, close]) {
  const bounds = control.getBoundingClientRect();
  check(bounds.width > 0 && bounds.left >= sheet.left && bounds.right <= sheet.right,
        control.textContent + ' was clipped by the sheet');
  check(control.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)),
        control.textContent + ' was covered');
}
tabs[1].click();
check(tabs[1].getAttribute('aria-selected') === 'true', 'Answered did not open');
close.click();
check(!dialog.open, 'Close library did not dismiss the sheet');
""", path="/qd/", viewport=(320, 800))

    def test_short_library_viewports_keep_search_results_and_header_reachable(self):
        self.add("resource", "Harbor chart", "Review the route.", "--url", "https://example.org/chart",
                 "--link-text", "Open chart")
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps(QUESTIONS[:1]))
        self.assertEqual(self.run_cli("mark", "Q2", "closed")[0], 0)
        port = self.start("/qd")
        for width in (568, 900):
            with self.subTest(width=width):
                self.check_answer_page(port, r"""
check(innerHeight === 140, 'Wrong fixture height');
button('open-library').click();
const dialog = button('library');
const tabs = [...dialog.querySelectorAll('[role="tab"]')];
const reach = control => {
  control.scrollIntoView({block:'center'});
  const bounds = control.getBoundingClientRect();
  check(control.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)),
        'The short library hid ' + (control.textContent || control.id));
};
for (const [index, query, href] of [[0, 'harbor', 'q1.html'], [1, 'tide', 'q2.html']]) {
  reach(tabs[index]);
  tabs[index].click();
  const panel = button(tabs[index].getAttribute('aria-controls'));
  const search = panel.querySelector('input');
  reach(search);
  search.focus();
  search.value = query;
  search.dispatchEvent(new Event('input', {bubbles:true}));
  reach(panel.querySelector('a[href="' + href + '"]'));
}
const close = dialog.querySelector('[data-close]');
reach(close);
close.click();
check(!dialog.open, 'Close library did not dismiss the panel');
""", path="/qd/", viewport=(width, 140))

    def test_desk_card_stretches_one_action_without_covering_its_links(self):
        question = {**QUESTIONS[1], "question": "Use https://example.org/options?",
                    "links": [{"text": "Review the chart", "url": "https://example.org/review"}]}
        self.add("questions", "Pier lights", "See https://example.org/notes", "--body-file", "-",
                 stdin=json.dumps([QUESTIONS[0], question]))
        self.add("questions", "Tide tables", "Check the latest plan.", "--body-file", "-",
                 stdin=json.dumps(QUESTIONS))
        self.assertEqual(self.run_cli("mark", "Q1.1", "closed")[0], 0)
        self.check_answer_page(self.start("/qd"), r"""
const [first, second] = document.querySelectorAll('.desk article');
const hit = node => {
  node.scrollIntoView({block:'center'});
  const rect = node.getClientRects()[0];
  return document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)?.closest('a');
};
for (const [item, destination, expectedLinks] of [[first, 'q2.html#Q2.1', []], [second, 'q1.html#Q1.2',
    ['https://example.org/notes', 'https://example.org/options', 'https://example.org/review']]]) {
  const links = [...item.querySelectorAll('a')];
  const action = links.find(link => link.getAttribute('href') === destination);
  check(action && links.length === expectedLinks.length + 1, 'A card had multiple actions or jumped to the wrong question');
  check(hit(item.querySelector('h3')) === action && hit(action) === action, 'The card and its action had different hit targets');
  action.focus();
  check(document.activeElement === action && getComputedStyle(item).outlineStyle === 'solid', 'The card lost its keyboard focus ring');
  for (const url of expectedLinks) {
    const link = links.find(anchor => anchor.getAttribute('href') === url);
    check(link && hit(link) === link, 'The stretched action covered an independent link');
  }
}
""", path="/qd/")

    def test_restored_desk_reloads_archive_membership_and_search(self):
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the Desk history browser test")
        for title in ("Pier lights", "Tide tables", "Harbor chart"):
            self.add("questions", title, "Choose an option.", "--body-file", "-", stdin=json.dumps(QUESTIONS[:1]))
        home = self.root / "home"
        inbox = home / "bin" / "fm-inbox.sh"
        inbox.parent.mkdir(parents=True)
        inbox.write_text("#!/bin/sh\nexit 0\n")
        inbox.chmod(0o700)
        finished = threading.Event()
        results = []
        handler = quarterdeck.make_handler("Quarterdeck", "/qd", home=home)

        class HistoryTestHandler(handler):
            def do_POST(self):
                if self.path == "/qd/history-test-result":
                    results.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                    self.send_text(200, "")
                    finished.set()
                else:
                    super().do_POST()

        with patch.object(quarterdeck, "make_handler", return_value=HistoryTestHandler):
            port = self.start("/qd")
        status, old_answer = self.post_answer(port, "Q2.1", "Saffron")
        self.assertEqual((status, old_answer["delivery"]), (200, "sent"))

        list_page = quarterdeck.list_page
        item_page = quarterdeck.item_page

        def instrumented_page(*args, **kwargs):
            return list_page(*args, **kwargs).replace("</body>", """
<script>
const report = message => fetch('history-test-result', {method:'POST', body:message});
const check = (condition, message) => { if (!condition) throw new Error(message); };
const dialog = document.getElementById('library');
const tab = document.getElementById('tab-answered');
const panel = document.getElementById('panel-answered');
const search = panel.querySelector('input');
const shown = () => [...panel.querySelectorAll('li:not([hidden]) a')].map(link => link.getAttribute('href')).join();
const find = text => {
  search.value = text;
  search.dispatchEvent(new Event('input', {bubbles:true}));
  return shown();
};
try {
  const desk = document.querySelector('main');
  const visits = Number(sessionStorage.getItem('item-visits'));
  if (!visits) {
    check(desk.textContent.includes('Pier lights'), 'The open item did not start on the Desk');
    document.getElementById('open-library').click();
    tab.click();
    check(find('saffron') === 'q2.html', 'The original answer did not match its search');
    panel.querySelector('a[href="q2.html"]').click();
  } else if (visits <= 2) {
    check(!desk.textContent.includes('Pier lights'), 'The restored Desk still showed the completed item');
    check(dialog.open && tab.getAttribute('aria-selected') === 'true' && !panel.hidden,
          'The library did not reopen on Answered after item visit ' + visits);
    check(search.value === 'saffron' && shown() === '', 'The saved search was lost or still used the old answer');
    check(panel.querySelector('a[href="q1.html"]'), 'The completed item was missing from Answered');
    check(find('ultramarine') === 'q2.html', 'The restored search did not find the corrected answer');
    if (visits === 1) window.addEventListener('pageshow', () => setTimeout(() => history.forward(), 0), {once:true});
    else window.addEventListener('pageshow', () => {
      dialog.addEventListener('close', () => {
        desk.querySelector('a[href="q3.html#Q3.1"]').click();
      }, {once:true});
      dialog.querySelector('[data-close]').click();
    }, {once:true});
  } else {
    check(visits === 3 && !dialog.open, 'Back from a Desk card reopened the dismissed lookup');
    if (sessionStorage.getItem('reloading-desk')) report('PASS');
    else window.addEventListener('pageshow', () => {
      sessionStorage.setItem('reloading-desk', 'yes');
      location.reload();
    }, {once:true});
  }
} catch (error) { report(error.message); }
</script>
</body>""")

        def instrumented_item(*args, **kwargs):
            return item_page(*args, **kwargs).replace("</body>", """
<script>
window.addEventListener('pageshow', async () => {
  const visits = Number(sessionStorage.getItem('item-visits')) + 1;
  sessionStorage.setItem('item-visits', visits);
  try {
    if (visits === 3 && !location.pathname.endsWith('/q3.html')) throw new Error('The Desk card did not open');
    if (visits === 1) {
      for (const payload of [
        {part:'Q1.1', answer:'Keep the lights', revision:''},
        {part:'Q2.1', answer:'Ultramarine', revision:""" + json.dumps(old_answer["revision"]) + """}
      ]) {
        const response = await fetch('answers', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify(payload)});
        if (!response.ok || (await response.json()).delivery !== 'sent') throw new Error('The fixture answer was not delivered');
      }
    }
    history.back();
  } catch (error) { fetch('history-test-result', {method:'POST', body:error.message}); }
});
</script>
</body>""")

        with patch.object(quarterdeck, "list_page", instrumented_page), patch.object(quarterdeck, "item_page", instrumented_item):
            with subprocess.Popen([chrome, "--headless", "--disable-background-networking", "--no-first-run",
                                   f"--user-data-dir={self.root / 'browser'}", f"http://127.0.0.1:{port}/qd/"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as browser:
                try:
                    self.assertTrue(finished.wait(10), "The browser did not complete the Desk history checks")
                finally:
                    browser.terminate()
        self.assertEqual(results, ["PASS"])

    def test_the_library_pop_out_searches_and_returns_focus(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-", stdin=json.dumps(QUESTIONS))
        self.add("questions", "Pier lights", "Pick the lamp colour.", "--body-file", "-", stdin=json.dumps(QUESTIONS))
        description = "A chart of the quay, showing slips and moorings. " * 8 + "See https://example.org/notes"
        self.add("resource", "Harbour map", description, "--url", "https://example.org/map", "--link-text", "Open the map")
        self.add("list", "Harbour supplies", "Tools for the pilot.", "--body-file", "-", stdin="Rope")
        home = self.root / "home"
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        port = self.start("/qd", home=home)
        with patch.object(quarterdeck, "now_text", return_value="2026-01-10T16:25:00Z"):
            self.assertEqual(self.post_answer(port, "Q2.1", "Use amber")[1]["delivery"], "sent")
        self.assertEqual(self.run_cli("mark", "Q2.2", "answered")[0], 0)
        for name in ("q1.html", "q2.html", "q3.html", "q4.html"):
            status, page, _ = self.fetch(port, f"/qd/{name}")
            self.assertEqual(status, 200)
            self.assertNotIn("<dialog", page)
            self.assertNotIn("Open library</button>", page)
            self.assertEqual(page.count('href="index.html"'), 2)
        self.check_answer_page(port, r"""
const opener = document.getElementById('open-library');
const dialog = document.getElementById('library');
const desk = [...document.querySelectorAll('.desk .work')];
check(desk.length === 1 && desk[0].textContent.includes('Tide decisions'), 'Only the unanswered item belongs on the Desk');
opener.click();
check(dialog.open, 'The library did not open');
const tabs = [...dialog.querySelectorAll('[role="tab"]')];
check(document.activeElement === tabs[0], 'Focus did not move to the first tab');
tabs[0].dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
check(tabs[1].getAttribute('aria-selected') === 'true' && document.activeElement === tabs[1], 'Arrow keys did not change tab');
const panel = document.getElementById('panel-answered');
check(!panel.hidden && document.getElementById('panel-references').hidden, 'The tab did not switch panels');
const search = document.getElementById('search-answered');
const shown = () => [...panel.querySelectorAll('li:not([hidden]) a')].map(link => link.getAttribute('href'));
const type = text => { search.value = text; search.dispatchEvent(new Event('input', {bubbles: true})); };
check(shown().join() === 'q2.html', 'The answered list was wrong');
check(dialog.querySelectorAll('input[type="search"]').length === 2 && !dialog.querySelector('input[type="date"]'),
      'The library does not have exactly one search per tab');
for (const query of ['pier lamp merge amber', 'Q2', 'q2.1', 'q2.2']) {
  type(query);
  check(shown().join() === 'q2.html', 'Failed to match ' + query);
  check(!panel.querySelector('[role="status"]').textContent, 'A successful search reported a match count');
  check(!/amber|Merge the tide tables/.test(panel.innerText), 'Search revealed private previews');
}
for (const query of ['q2.0', 'q2.9', 'q2.01', 'q20']) {
  type(query);
  check(shown().length === 0 && panel.innerText.includes('No results'), 'Matched a missing ID ' + query);
}
tabs[0].click();
const references = document.getElementById('search-references');
references.value = 'chart';
references.dispatchEvent(new Event('input', {bubbles: true}));
check([...document.querySelectorAll('#panel-references .lib-item')].filter(row => !row.hidden).length === 1, 'References search failed');
check(!references.closest('[role="tabpanel"]').querySelector('[role="status"]').textContent, 'References search reported a match count');
const notes = dialog.querySelector('a[href="https://example.org/notes"]');
notes.scrollIntoView({block: 'nearest'});
const rect = notes.getClientRects()[0];
check(notes.contains(document.elementFromPoint(rect.left + 2, rect.top + 2)), 'A long description clipped its link');
references.value = 'q3.1';
references.dispatchEvent(new Event('input', {bubbles: true}));
check(![...document.querySelectorAll('#panel-references li')].some(row => !row.hidden), 'A reference matched a question ID');
await new Promise(resolve => { dialog.addEventListener('close', resolve, {once: true}); dialog.close(); });
check(document.activeElement === opener, 'Focus did not return to the library button');
""", path="/qd/", viewport=(900, 800))

    def test_undo_reverses_one_option_action_and_preserves_later_drafts(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps([QUESTIONS[0]] * 4))
        self.check_answer_page(self.start("/qd"), """
card(1).querySelector('input[data-choice="Hold"]').click();
button('suggest').click();
typeAnswer(2, 'Use the next tide');
typeAnswer(4, 'Consult the pilot');
button('undo').click();
check(field(1).value === 'Hold', 'Undo also reversed an earlier option');
check(field(2).value === 'Use the next tide', 'Undo removed a later draft on a suggested question');
check(field(3).value === '', 'Undo left an untouched suggestion in place');
check(field(4).value === 'Consult the pilot', 'Undo removed another later draft');
button('undo').click();
check(field(1).value === '', 'Second undo did not reverse the earlier option');
check(field(2).value === 'Use the next tide' && field(4).value === 'Consult the pilot',
      'Second undo removed later drafts');
check(button('undo').hidden, 'Undo history was not empty');
""")

    def test_folded_correction_undo_focus_and_rejection_respect_the_row(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps([*QUESTIONS, QUESTIONS[0]]))
        self.check_answer_page(self.start("/qd"), r"""
window.fetch = async (url, options) => {
  const {answer} = JSON.parse(options.body);
  return {ok: true, json: async () => ({answer, state: 'answered', revision: 'saved-revision',
    answered_at: '2026-01-12T15:00:00Z', delivery: 'sent'})};
};
typeAnswer(1, 'Hold');
typeAnswer(2, 'Blue');
await sendBatch();
const first = card(1).closest('details');
const second = card(2).closest('details');
const summary = second.querySelector('summary');
check(!first.open && !second.open, 'The delivered questions should start folded');
summary.click();
const red = card(2).querySelector('[data-choice="Red"]');
const undo = button('undo');
red.click();
undo.focus();
undo.click();
check(document.activeElement === field(2) && field(2).value === 'Blue', 'Undo lost focus instead of returning to the affected field');
red.click();
summary.click();
undo.focus();
undo.click();
check(document.activeElement === summary && !second.open, 'Undo did not focus the collapsed row summary');
summary.click();
red.click();
window.fetch = async () => {
  summary.click();
  return {ok: false, status: 409, text: async () => 'Question changed. Reload before sending.'};
};
await sendBatch();
const error = document.getElementById(field(2).getAttribute('aria-describedby'));
check(second.open && !error.hidden && error.textContent === 'Question changed. Reload before sending.',
      'The rejected correction did not reveal its inline error');
summary.click();
typeAnswer(3, 'Merge');
check(!second.open && !first.open, 'An unrelated edit reopened a folded question');
summary.click();
check(!error.hidden && field(2).value === 'Red', 'Closing the row discarded its rejected draft or error');
""")

    def test_batch_conflict_keeps_its_draft_and_sends_independent_answers(self):
        with patch.object(quarterdeck, "now_text", return_value="2026-01-10T16:25:00Z"):
            self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                     stdin=json.dumps(QUESTIONS * 2 + QUESTIONS[:1]))
        home = self.root / "home"
        port = self.start("/qd", home=home)
        self.assertEqual(self.post_answer(port, "Q1.5", "Merge")[1]["delivery"], "pending")
        self.assertEqual(self.run_cli("mark", "Q1.5", "open")[0], 0)
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        with patch.object(quarterdeck, "now_text", return_value="2026-01-10T16:25:00Z"):
            self.assertEqual(self.post_answer(port, "Q1.4", "\ufeffRed")[1]["delivery"], "sent")
        self.assertEqual(self.run_cli("mark", "Q1.4", "open")[0], 0)
        self.check_answer_page(port, r"""
const savedDay = document.querySelector('main > .meta time');
check(savedDay.textContent === 'Jan 11, 2026', 'The saved date was not shown in local time');
const originalAnswerTime = card(4).querySelector('.saved-answer time');
check(originalAnswerTime.textContent.replace(/\s+/g, ' ') === 'Jan 11, 2026, 1:25 AM',
      'Wrong local time for the saved answer');
check(button('send').textContent === 'Send 1 answer', 'Only the reopened pending answer should be queued at page load');
typeAnswer(1, 'Hold <for the pilot>');
typeAnswer(2, 'Blue');
typeAnswer(3, 'Merge');
check(button('send').textContent === 'Send 4 answers', 'The batch should include three drafts and one pending retry');
const sent = [];
window.fetch = async (url, options) => {
  const {part, answer} = JSON.parse(options.body);
  sent.push(part);
  if (part === 'Q1.2') {
    return {ok: false, status: 409, text: async () => 'Question changed. Reload before sending.'};
  }
  return {ok: true, json: async () => ({
    answer, state: part === 'Q1.5' ? 'open' : 'answered', revision: 'saved-revision',
    answered_at: '2026-01-12T15:00:00Z', delivery: 'sent'
  })};
};
await sendBatch();
check(sent.join(',') === 'Q1.1,Q1.2,Q1.3,Q1.5', 'The conflict blocked another draft or the retry');
check(card(1).querySelector('.badge').textContent === 'Answered', 'The saved badge was stale');
const saved = card(1).querySelector('.saved-answer');
check(!saved.hidden && saved.querySelector('.record').textContent === 'Hold <for the pilot>',
      'The saved answer was missing or interpreted as HTML');
check(saved.querySelector('.delivery').textContent === 'Sent', 'The delivery label was stale');
check(saved.querySelector('time').dateTime === '2026-01-12T15:00:00Z', 'The saved timestamp was stale');
check(saved.querySelector('time').textContent.replace(/\s+/g, ' ') === 'Jan 13, 2026, 12:00 AM',
      'Wrong local time after sending');
check(card(5).querySelector('.badge').textContent === 'Open' &&
      card(5).querySelector('.delivery').textContent === 'Sent', 'The retry did not preserve the reopened state');
check(card(5).parentElement.id === 'questions', 'The open retry was folded');
check(document.querySelector('.progress').textContent === '3 of 5 open', 'Progress was stale');
check(field(2).value === 'Blue', 'The conflicting draft was lost');
check(button('send').disabled, 'A saved, conflicting or unchanged reopened answer was still queued');
check(card(2).textContent.includes('Question changed. Reload before sending.'), 'The conflict was hidden');
check(document.querySelector('[role="status"]').textContent === '1 answer rejected.', 'The toolbar repeated the conflict');
typeAnswer(4, 'Blue');
await sendBatch();
check(sent.join(',') === 'Q1.1,Q1.2,Q1.3,Q1.5,Q1.4', 'The next send retried the conflicted draft');
check(field(2).value === 'Blue', 'A later send discarded the conflicting draft');
check(card(2).textContent.includes('Question changed. Reload before sending.'), 'A later send cleared the inline conflict');
check(document.querySelector('[role="status"]').textContent === 'Answers saved.', 'The toolbar counted an earlier conflict');
""")

    def test_batch_rejection_preserves_an_editable_draft(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps(QUESTIONS))
        self.check_answer_page(self.start("/qd"), r"""
const invalid = 'Hold\u0085until morning';
typeAnswer(1, invalid);
card(2).querySelector('[data-choice="Blue"]').click();
const sent = [];
window.fetch = async (url, options) => {
  const {part, answer} = JSON.parse(options.body);
  sent.push([part, answer]);
  if (answer === invalid) {
    return {ok: false, status: 400, text: async () => 'Check the question and answer.'};
  }
  return {ok: true, json: async () => ({
    answer, state: 'answered', revision: 'saved-revision',
    answered_at: '2026-01-12T15:00:00Z', delivery: 'sent'
  })};
};
await sendBatch();
const error = document.getElementById(field(1).getAttribute('aria-describedby'));
check(card(1).contains(error) && !error.hidden && error.textContent === 'Check the question and answer.',
      'The rejection was missing from its question');
check(document.querySelector('[role="status"]').textContent === '1 answer rejected.', 'The toolbar repeated the draft error');
check(field(1).value === invalid && !field(1).disabled && button('send').disabled,
      'The rejected draft was lost, locked or left queued');
check(card(2).querySelector('.record').textContent === 'Blue', 'The valid answer was blocked');
card(1).querySelector('[data-choice="Hold"]').click();
check(error.hidden, 'The rejection remained after correcting the draft');
check(document.querySelector('[role="status"]').textContent === '', 'The toolbar retained the corrected rejection');
await sendBatch();
check(JSON.stringify(sent) === JSON.stringify([['Q1.1', invalid], ['Q1.2', 'Blue'], ['Q1.1', 'Hold']]),
      'The corrected answer was lost or the saved answer was resent');
""")

    def test_successful_send_preserves_suggestion_text_without_reloading(self):
        question = {**QUESTIONS[0], "suggestion": "\ufeffMerge"}
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps([question, question, QUESTIONS[1]]))
        self.check_answer_page(self.start("/qd"), r"""
card(1).querySelector('[data-suggested]').click();
button('suggest').click();
typeAnswer(3, '\u0085');
check(button('send').textContent === 'Send 2 answers', 'A whitespace-only answer was queued');
const sent = [];
window.fetch = async (url, options) => {
  const {part, answer} = JSON.parse(options.body);
  sent.push([part, answer]);
  return {ok: true, json: async () => ({
    answer, state: 'answered', revision: 'saved-revision', answered_at: '2026-01-12T15:00:00Z', delivery: 'pending'
  })};
};
await sendBatch();
check(JSON.stringify(sent) === JSON.stringify([['Q1.1', '\ufeffMerge'], ['Q1.2', '\ufeffMerge']]),
      'The submitted text did not preserve both suggestions');
check(card(1).querySelector('.delivery').textContent === 'Not delivered', 'Pending delivery was hidden');
check(!field(3).disabled && field(3).value === '\u0085', 'The other draft or its editor was lost');
check(!button('send').disabled && button('send').textContent === 'Send 2 answers', 'Pending answers cannot be retried');
""")

    def test_binding_uses_only_an_explicit_or_single_home(self):
        path = self.root / "legacy.json"
        homes = [{"path": str(self.root / "orchard")}, {"path": "garden"}]
        path.write_text(json.dumps({"homes": homes}))
        self.assertIsNone(quarterdeck.answer_home(quarterdeck.load_config(path), path))
        path.write_text(json.dumps({"homes": homes[:1]}))
        self.assertEqual(self.root / "orchard", quarterdeck.answer_home(quarterdeck.load_config(path), path))
        explicit = str(self.root / "grove")
        path.write_text(json.dumps({"homes": homes[:1], "firstmate_home": explicit}))
        self.assertEqual(Path(explicit), quarterdeck.answer_home(quarterdeck.load_config(path), path))

    def test_render_and_service_unit_ignore_unresolvable_legacy_inbox(self):
        config = self.root / "config" / "quarterdeck.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"homes": [{"path": "~quarterdeck-fixture-no-such-user/inbox"}]}))
        for command in ("render", "service", "url"):
            with self.subTest(command=command):
                code, _, error = self.run_cli(command)
                self.assertEqual(code, 0, error)

    def test_malformed_inbox_paths_follow_config_binding_rules(self):
        config = self.root / "quarterdeck.json"
        for path in ("~quarterdeck-fixture-no-such-user/inbox", str(self.root / "home\0"), "relative/home"):
            with self.subTest(binding="explicit", path=path):
                config.write_text(json.dumps({"firstmate_home": path}))
                with patch.object(quarterdeck, "make_server", side_effect=AssertionError("unexpected server start")):
                    code, _, error = self.run_cli("serve", "--config", str(config))
                self.assertEqual(code, 2)
                self.assertIn("config firstmate_home must be a valid absolute path", error)
        for path in ("~quarterdeck-fixture-no-such-user/inbox", str(self.root / "home\0")):
            with self.subTest(binding="legacy", path=path):
                config.write_text(json.dumps({"homes": [{"path": path}]}))
                home = quarterdeck.answer_home(quarterdeck.load_config(config), config)
                self.assertIsNone(home)

    def test_framing_policy_applies_only_to_pages_with_answer_controls(self):
        self.add_all_kinds()
        port = self.start("/qd")
        self.assertEqual(self.run_cli("mark", "Q3.1", "closed")[0], 0)
        status, _, headers = self.fetch(port, "/qd/q3.html")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Security-Policy"), "frame-ancestors 'none'")
        self.assertEqual(self.run_cli("mark", "Q3.2", "closed")[0], 0)
        for method, path, expected in (
            ("GET", "/qd/", 200), ("GET", "/qd/q1.html", 200), ("GET", "/qd/q2.html", 200),
            ("GET", "/qd/q3.html", 200), ("GET", "/qd/icon.svg", 200), ("GET", "/qd/q9.html", 404),
            ("POST", "/qd/answers", 403),
        ):
            with self.subTest(method=method, path=path):
                status, _, headers = self.fetch(port, path, method=method)
                self.assertEqual(status, expected)
                self.assertNotIn("Content-Security-Policy", headers)

    def test_pages_follow_the_store_without_a_restart(self) -> None:
        port = self.start()
        status, body, _headers = self.fetch(port, "/")
        self.assertEqual(status, 200)
        self.assertIn("Nothing is saved yet.", body)
        self.add_all_kinds()
        self.assertIn("Tide launch decisions", self.fetch(port, "/")[1])
        status, body, _headers = self.fetch(port, "/q3.html")
        self.assertEqual(status, 200)
        self.assertIn("Q3.2", body)
        for path in ("/q9.html", "/Q3", "/q3", "/Q0", "/q03.html", "/done.html"):
            with self.subTest(path=path):
                self.assertEqual(self.fetch(port, path)[0], 404)
        self.assertEqual(self.fetch(port, "/classic.html")[0], 404)
        self.assertEqual(self.fetch(port, "/icon.svg")[0], 200)
        self.assertEqual(self.fetch(port, "/", method="POST")[0], 405)

    def test_base_path_redirects_and_foreign_hosts_are_refused(self) -> None:
        port = self.start("/qd")
        status, _body, headers = self.fetch(port, "/qd")
        self.assertEqual((status, headers.get("Location")), (308, "/qd/"))
        self.assertEqual(self.fetch(port, "/qd/")[0], 200)
        self.assertEqual(self.fetch(port, "/q3.html")[2].get("Location"), "/qd/q3.html")
        self.assertEqual(self.fetch(port, "/qd/", host="attacker.example")[0], 403)

    def test_legacy_config_delivers_to_a_relative_home_without_ingesting_it(self) -> None:
        self.add_all_kinds()
        home = self.root / "firstmate"
        (home / "bin").mkdir(parents=True)
        (home / "backlog.md").write_text("Legacy work item")
        script = home / "bin" / "fm-inbox.sh"
        script.write_text('#!/bin/sh\nprintf "%s" "$6" > received-note\n')
        script.chmod(0o700)
        config = self.root / "config" / "legacy.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"homes": [{"path": "../firstmate"}], "captain_waits": {}}))
        with self.serve_cli("--config", str(config), "--base-path", "/qd",
                            "--allow-marks", "--watch-waits", "--no-snapshot", "--home", str(home)) as port:
            status, body, _headers = self.fetch(port, "/qd/")
            self.assertEqual(status, 200)
            self.assertIn("Tide launch decisions", body)
            self.assertNotIn("Legacy work item", body)
            self.assertNotIn(str(home), body)
            self.assertNotIn('id="refresh-workers"', body)
            self.assertEqual(self.fetch(port, "/qd/workers.json")[0], 404)
            before = quarterdeck.store_path().read_bytes()
            self.assertEqual(self.fetch(port, "/", method="POST")[0], 405)
            self.assertEqual(quarterdeck.store_path().read_bytes(), before)
            status, record = self.post_answer(port, "Q3.1", "Hold")
            self.assertEqual((status, record["delivery"]), (200, "sent"))
            self.assertEqual((home / "received-note").read_text(), "Q3.1 Hold")

    def test_damaged_store_returns_an_error_without_server_paths(self) -> None:
        self.add_all_kinds()
        path = quarterdeck.store_path()
        damaged = self.store()
        del damaged["items"][0]["body"]
        path.write_text(json.dumps(damaged))
        port = self.start("/qd")
        for route in ("/qd/", "/qd/q2.html"):
            status, body, _headers = self.fetch(port, route)
            self.assertEqual(status, 500)
            self.assertIn("could not read its item store", body)
            self.assertNotIn(str(path), body)
        self.assertEqual(self.fetch(port, "/qd/Q1")[0], 404)
        self.assertEqual(self.post_answer(port, "Q3.1", "Hold"), (500, b"Could not save the answer."))


def snapshot_task(key, *, kind="ship", state="working", exists=True, title="Mapping the harbor trail", project="Copper Orchard",
                  age=540, decisions=(), pr=None, alive="not_checked", freshness="fresh"):
    return {"id": key, "kind": kind, "project": f"/projects/{project}",
            "current_state": {"state": state, "freshness": freshness},
            "endpoint": {"exists": exists, "agent_alive": alive},
            "hints": {"open_decisions": [{"key": d} for d in decisions]},
            "paths": {"status_log": {"last_event": {"age_seconds": age}}},
            "pr": {"url": pr}, "backlog": {"title": title, "repo": project}}


SNAPSHOT_TIME = "2026-03-02T13:40:00Z"


class WorkersTests(StoreCase):
    """The workers section reads a fictional home's snapshot and nothing else."""

    start = ServeTests.start
    fetch = ServeTests.fetch

    def make_home(self, tasks=None, *, body=None, code=0) -> Path:
        home = self.root / "home"
        script = home / "bin" / "fm-fleet-snapshot.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        fixture = home / "snapshot.json"
        fixture.write_text(body if body is not None else json.dumps({"generated": SNAPSHOT_TIME, "tasks": tasks or []}))
        script.write_text('#!/bin/sh\nset -eu\ntest "$#" = 1 && test "$1" = --local || exit 2\n'
                          'test "$PWD" = "$FM_HOME" && test -z "${FM_OTHER:-}" || exit 3\n'
                          f'cat "$FM_HOME/snapshot.json"\nexit {code}\n')
        script.chmod(0o700)
        return home

    def workers(self, port, path="/qd/workers.json"):
        status, body, _ = self.fetch(port, path)
        return status, json.loads(body)

    def test_lists_only_workers_with_a_live_terminal_and_the_second_mate_roster(self) -> None:
        project_prefix = "Northern harbor survey " * 4
        activity_prefix = "Charting the shipping lanes and recording the results. " * 8
        home = self.make_home([
            snapshot_task("Wren", state="done", exists=False, pr="not a URL"),
            snapshot_task("Wren", exists=None, decisions=[None]),
            {**snapshot_task("Wren", decisions=["map-scale"], pr="https://example.com/pull/7"), "current_state": []},
            snapshot_task("Brackén Scout", state="blocked", title=activity_prefix + "East",
                          project=project_prefix + "East", alive=[]),
            snapshot_task(None, state="done", exists=False),
            {**snapshot_task("Heron", state="paused", age=1e30), "project": None,
             "backlog": {"repo": project_prefix + "West", "title": activity_prefix + "West"}},
            {**snapshot_task("Lark", freshness="stale", state=[], age=378691200), "backlog": {"repo": []}},
            snapshot_task("Skerry", state=[], exists=None),
            snapshot_task("Skerry", kind="secondmate", alive="alive", state=[], freshness=[], decisions=["trail-route"], age=[]),
            {**snapshot_task("Skerry", kind="secondmate", pr="not a URL"), "endpoint": None},
            snapshot_task("Tern", kind="secondmate", alive="dead"),
            {**snapshot_task("Tern", title=[]), "endpoint": None},
            snapshot_task("Cove Mate", kind="secondmate", alive="unknown", exists=None, state="unknown"),
            snapshot_task("Pike", kind="secondmate", exists=False, alive="not_checked"),
            snapshot_task("Snipe", kind="secondmate", exists=False, alive="alive"),
            snapshot_task("Kite", kind="secondmate", exists=False, alive=[], state=[]),
            snapshot_task("Wren", decisions=[None], exists=None),
        ])
        port = self.start("/qd", workers_home=home)
        status, data = self.workers(port)
        self.assertEqual(status, 200)
        self.assertEqual(data["observed_at"], SNAPSHOT_TIME)
        rows = {row["name"]: row for row in data["workers"]}
        self.assertEqual(len(data["workers"]), 4)
        self.assertEqual(list(rows), ["Wren", "Brackén Scout", "Heron", "Lark"])
        self.assertEqual([rows[n]["status"] for n in rows], ["Question asked", "Stuck", "Unknown", "Unknown"])
        self.assertEqual(rows["Wren"]["project"], "Copper Orchard")
        self.assertEqual(rows["Wren"]["activity"], "Mapping the harbor trail")
        self.assertEqual(rows["Wren"]["active_at"], "2026-03-02T13:31:00Z")
        for name, direction in (("Brackén Scout", "East"), ("Heron", "West")):
            self.assertEqual(rows[name]["project"], project_prefix + direction)
            self.assertEqual(rows[name]["activity"], activity_prefix + direction)
        self.assertEqual(rows["Lark"]["project"], "Copper Orchard")
        self.assertEqual(rows["Lark"]["active_at"], "2014-03-02T13:40:00Z")
        self.assertIsNone(rows["Heron"]["active_at"])
        self.assertTrue(all("contact_at" not in row for row in rows.values()))
        self.assertEqual(rows["Wren"]["links"], [{"text": "Pull request", "href": "https://example.com/pull/7"}])
        mates = {row["name"]: row for row in data["second_mates"]}
        self.assertEqual({n: m["status"] for n, m in mates.items()},
                         {"Skerry": "Question asked", "Tern": "Unreachable", "Cove Mate": "Unknown",
                          "Pike": "Unreachable", "Snipe": "Unreachable", "Kite": "Unreachable"})
        self.assertEqual(len(data["second_mates"]), 6)
        self.assertEqual(mates["Skerry"]["contact_at"], SNAPSHOT_TIME)
        self.assertTrue(all("active_at" not in row for row in mates.values()))
        for name in ("Tern", "Cove Mate", "Pike", "Snipe", "Kite"):
            self.assertIsNone(mates[name]["contact_at"])

    def test_missing_facts_say_activity_unavailable_and_invent_nothing(self) -> None:
        task = {"id": "Plover", "kind": "scout", "endpoint": {"exists": True}}
        null_facts = {**task, "id": "Merlin", "project": None, "backlog": {"title": None, "repo": None},
                      "paths": {"status_log": {"last_event": None}}, "pr": {"url": None}}
        port = self.start("/qd", workers_home=self.make_home([task, null_facts]))
        status, payload = self.workers(port)
        self.assertEqual(status, 200)
        self.assertEqual([row["name"] for row in payload["workers"]], ["Plover", "Merlin"])
        for row in payload["workers"]:
            self.assertEqual((row["status"], row["activity"], row["project"], row["active_at"], row["links"]),
                             ("Unknown", "Activity unavailable", None, None, []))

    def test_an_empty_snapshot_is_an_empty_list(self) -> None:
        port = self.start("/qd", workers_home=self.make_home([]))
        self.assertEqual(self.workers(port)[1], {"observed_at": SNAPSHOT_TIME, "workers": [], "second_mates": []})

    def test_a_valid_second_mate_resolves_an_unreliable_duplicate_in_either_order(self) -> None:
        valid = snapshot_task("Skerry", kind="secondmate", alive="alive")
        port = self.start("/qd", workers_home=self.make_home())
        for endpoint in (None, {"exists": 0}):
            unreliable = {**valid, "endpoint": endpoint}
            for tasks in ([unreliable, valid], [valid, unreliable]):
                with self.subTest(endpoint=endpoint, unreliable_first=tasks[0] is unreliable):
                    self.make_home(tasks)
                    status, payload = self.workers(port)
                    self.assertEqual(status, 200)
                    self.assertEqual(payload["workers"], [])
                    self.assertEqual([(row["name"], row["status"], row["contact_at"]) for row in payload["second_mates"]],
                                     [("Skerry", "Working", SNAPSHOT_TIME)])

    def test_failures_report_a_failed_read_and_never_a_partial_list(self) -> None:
        valid = snapshot_task("Wren")
        port = self.start("/qd", workers_home=self.make_home([valid]))
        self.assertEqual(self.workers(port)[0], 200)
        cases = {
            "exit": {"tasks": [valid], "code": 1},
            "text": {"body": "not json"},
            "shape": {"body": json.dumps({"tasks": "x", "generated": SNAPSHOT_TIME})},
            "time": {"body": json.dumps({"tasks": [], "generated": "yesterday"})},
            "calendar": {"body": json.dumps({"tasks": [], "generated": "2026-02-30T13:40:00Z"})},
            "task": {"tasks": [valid, None]},
            "identity": {"tasks": [{**valid, "id": None}]},
            "blank identity": {"tasks": [{**valid, "id": "  "}]},
            "control in identity": {"tasks": [snapshot_task("Wr\x00en")]},
            "surrogate in identity": {"tasks": [snapshot_task("Wr\ud800en")]},
            "kind": {"tasks": [{**valid, "kind": None}]},
            "endpoint": {"tasks": [{**valid, "endpoint": None}]},
            "unresolved second mate": {"tasks": [valid, {**valid, "kind": "secondmate", "endpoint": None}]},
            "unobserved": {"tasks": [snapshot_task("Wren", exists=False), snapshot_task("Wren", exists=None)]},
            "nonboolean": {"tasks": [snapshot_task("Wren", exists=0)]},
            "state": {"tasks": [snapshot_task("Wren", state={})]},
            "freshness": {"tasks": [snapshot_task("Wren", freshness=[])]},
            "decisions": {"tasks": [{**valid, "hints": {"open_decisions": "map-scale"}}]},
            "null decision": {"tasks": [{**valid, "hints": {"open_decisions": [None]}}]},
            "missing decision key": {"tasks": [{**valid, "kind": "secondmate", "hints": {"open_decisions": [{}]}}]},
            "invalid decision key": {"tasks": [snapshot_task("Wren", decisions=[7])]},
            "blank decision key": {"tasks": [snapshot_task("Wren", decisions=[" "])]},
            "contact": {"tasks": [valid, snapshot_task("Tern", kind="secondmate", alive=[])]},
            "project": {"tasks": [{**valid, "project": []}]},
            "backlog": {"tasks": [{**valid, "backlog": []}]},
            "repo": {"tasks": [{**valid, "project": None, "backlog": {"repo": []}}]},
            "title": {"tasks": [snapshot_task("Wren", title=[])]},
            "paths": {"tasks": [{**valid, "paths": []}]},
            "status log": {"tasks": [{**valid, "paths": {"status_log": []}}]},
            "activity event": {"tasks": [{**valid, "paths": {"status_log": {"last_event": []}}}]},
            "activity age type": {"tasks": [snapshot_task("Wren", age="540")]},
            "activity age boolean": {"tasks": [snapshot_task("Wren", age=True)]},
            "activity age range": {"tasks": [snapshot_task("Wren", age=-1)]},
            "activity age infinity": {"tasks": [snapshot_task("Wren", age=float("inf"))]},
            "pull request": {"tasks": [{**valid, "pr": []}]},
            "pull request URL type": {"tasks": [snapshot_task("Wren", pr=[])]},
            "pull request URL format": {"tasks": [snapshot_task("Wren", pr="not a URL")]},
        }
        for name, fixture in cases.items():
            with self.subTest(name):
                self.make_home(**fixture)
                status, body = self.workers(port)
                self.assertEqual((status, list(body)), (502, ["error"]))
        self.make_home([snapshot_task("Wren", exists=False)])
        self.assertEqual(self.workers(port), (200, {"observed_at": SNAPSHOT_TIME, "workers": [], "second_mates": []}))

    def test_a_slow_snapshot_is_stopped(self) -> None:
        home = self.make_home([])
        lifetime = home / "lifetime"
        os.mkfifo(lifetime)
        (home / "bin" / "fm-fleet-snapshot.sh").write_text(
            '#!/bin/sh\nset -eu\nexec 3>lifetime >/dev/null\n'
            'sleep 30 &\nchild=$!\nprintf "%s %s\\n" "$$" "$child" > started\nwait "$child"\n')
        port = self.start("/qd", workers_home=home)
        reader = os.open(lifetime, os.O_RDONLY | os.O_NONBLOCK)
        try:
            with patch.object(quarterdeck, "SNAPSHOT_SECONDS", 0.5):
                self.assertEqual(self.workers(port)[0], 502)
            self.assertEqual(len((home / "started").read_text().split()), 2)
            self.assertTrue(select.select([reader], [], [], 1)[0], "snapshot shell or child still holds the pipe open")
            self.assertEqual(os.read(reader, 1), b"")
        finally:
            if (home / "started").exists() and not select.select([reader], [], [], 0)[0]:
                shell, child = map(int, (home / "started").read_text().split())
                for pid in (shell, child):
                    with suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGKILL)
            os.close(reader)

    def test_a_busy_snapshot_rejects_the_next_request(self) -> None:
        port = self.start("/qd", workers_home=self.make_home([]))
        entered, release = threading.Event(), threading.Event()
        run_snapshot = quarterdeck.run_snapshot

        def waiting_snapshot(home):
            entered.set()
            release.wait(timeout=10)
            return run_snapshot(home)

        with patch.object(quarterdeck, "run_snapshot", waiting_snapshot), ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.workers, port)
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(self.workers(port)[0], 502)
            finally:
                release.set()
            self.assertEqual(first.result()[0], 200)
        self.assertEqual(self.workers(port)[0], 200)

    def test_the_home_is_left_untouched_and_fm_variables_are_not_passed_on(self) -> None:
        home = self.make_home([snapshot_task("Wren")])
        before = sorted((str(p), p.stat().st_mtime_ns) for p in home.rglob("*"))
        port = self.start("/qd", workers_home=home)
        with patch.dict(os.environ, {"FM_OTHER": "elsewhere"}):
            self.assertEqual(self.workers(port)[0], 200)
        self.assertEqual(before, sorted((str(p), p.stat().st_mtime_ns) for p in home.rglob("*")))

    def test_without_a_home_there_is_no_endpoint_and_the_page_says_so(self) -> None:
        port = self.start("/qd")
        self.assertEqual(self.fetch(port, "/qd/workers.json")[0], 404)
        page = self.fetch(port, "/qd/")[1]
        self.assertIn("Workers show here once firstmate_home names a Firstmate home.", page)
        self.assertNotIn("refresh-workers", page)

    def test_the_workers_section_follows_the_desk_and_obeys_host_checks(self) -> None:
        port = self.start("/qd", workers_home=self.make_home([]))
        page = self.fetch(port, "/qd/")[1]
        self.assertEqual(page.count("<h1>"), 1)
        self.assertLess(page.index("<h1>Desk</h1>"), page.index("Workers"))
        self.assertIn('id="refresh-workers">Refresh workers</button>', page)
        self.assertEqual(self.fetch(port, "/qd/workers.json", host="intruder.example")[0], 403)

    def test_the_page_refreshes_on_entry_and_on_the_button_only(self) -> None:
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the workers refresh browser test")
        home = self.make_home([snapshot_task("Wren", decisions=["map-scale"])])
        port = self.start("/qd", workers_home=home)
        next_snapshot = {
            "generated": "2026-03-02T13:39:00Z",
            "tasks": [snapshot_task("Wren", exists=False), snapshot_task("Bracken", state="paused")],
        }
        list_page = quarterdeck.list_page
        run_snapshot = quarterdeck.run_snapshot

        def advance_fixture(home):
            snapshot = run_snapshot(home)
            if snapshot["generated"] == SNAPSHOT_TIME:
                (home / "snapshot.json").write_text(json.dumps(next_snapshot))
            return snapshot

        def instrumented_page(*args, **kwargs):
            return list_page(*args, **kwargs).replace("</body>", """
<output id="browser-result">Incomplete</output>
<script>
const result = document.getElementById('browser-result');
const statusLine = document.getElementById('workers-status');
const roster = document.getElementById('workers-list');
const button = document.getElementById('refresh-workers');
const settled = () => new Promise(resolve => {
  const watcher = new MutationObserver(() => {
    if (!button.hasAttribute('aria-disabled')) {
      watcher.disconnect();
      resolve();
    }
  });
  watcher.observe(button, {attributes: true});
});
const requestCount = () => performance.getEntriesByName(new URL('workers.json', location.href).href).length;
const check = (condition, message) => { if (!condition) throw new Error(message); };
(async () => {
  await settled();
  check(requestCount() === 1, 'Expected one worker request on entry');
  check(roster.textContent.includes('Wren') && !roster.textContent.includes('Bracken'), 'The initial roster did not appear');
  check(roster.querySelector('.status.exception')?.textContent === 'Question asked', 'The exception badge was missing');
  check(statusLine.querySelector('time')?.dateTime === '2026-03-02T13:40:00Z', 'The initial observation time was wrong');
  await new Promise(resolve => setTimeout(resolve, 61000));
  check(requestCount() === 1, 'A worker request ran while the page was idle');
  const refreshed = settled();
  button.click();
  await refreshed;
  check(requestCount() === 2, 'The refresh button did not make exactly one worker request');
  check(!roster.textContent.includes('Wren') && roster.textContent.includes('Bracken'), 'The refreshed roster did not replace the old one');
  check(roster.querySelector('.status')?.textContent === 'Unknown' && !roster.querySelector('.status.exception'),
        'Unknown did not use the ordinary badge');
  check(statusLine.querySelector('time')?.dateTime === '2026-03-02T13:39:00Z', 'The refresh did not show the corrected observation time');
  result.textContent = 'PASS';
})().catch(error => { result.textContent = error.message; });
</script>
</body>""")

        with patch.object(quarterdeck, "list_page", instrumented_page), patch.object(quarterdeck, "run_snapshot", advance_fixture):
            result = subprocess.run([chrome, "--headless", "--disable-background-networking", "--no-first-run",
                                     f"--user-data-dir={self.root / 'browser'}", "--dump-dom", "--virtual-time-budget=65000",
                                     f"http://127.0.0.1:{port}/qd/"],
                                    capture_output=True, text=True, timeout=30, check=True)
        browser_result = result.stdout.partition('<output id="browser-result">')[2].partition("</output>")[0]
        self.assertEqual(browser_result, "PASS", result.stderr)

    def test_explicit_home_enables_workers_only_when_serving(self) -> None:
        home = self.make_home([snapshot_task("Wren")])
        config = self.root / "quarterdeck.json"
        config.write_text(json.dumps({"firstmate_home": str(home)}))
        out = self.root / "out" / "index.html"
        code, _, error = self.run_cli("render", "--config", str(config), "--output", str(out))
        self.assertEqual(code, 0, error)
        self.assertNotIn("Workers", out.read_text())

        with self.serve_cli("--config", str(config), "--base-path", "/qd") as port:
            status, page, _ = self.fetch(port, "/qd/")
            self.assertEqual(status, 200)
            self.assertIn('id="refresh-workers">Refresh workers</button>', page)
            status, body, _ = self.fetch(port, "/qd/workers.json")
            self.assertEqual(status, 200)
            self.assertEqual([worker["name"] for worker in json.loads(body)["workers"]], ["Wren"])


class ServiceAndHelpTests(StoreCase):
    def test_service_unit_runs_serve_without_old_flags(self) -> None:
        args = quarterdeck.make_parser().parse_args(["service", "--host", "127.0.0.1", "--port", "9000", "--base-path", "/qd"])
        unit = ConfigParser(interpolation=None)
        unit.read_string(quarterdeck.service_unit(args))
        self.assertEqual(unit["Unit"]["Description"], "Quarterdeck page of saved items")
        self.assertEqual(unit["Unit"]["After"], "network.target")
        service = unit["Service"]
        self.assertEqual(shlex.split(service["ExecStart"]),
                         [sys.executable, str(ROOT / "quarterdeck.py"), "serve",
                          "--host", "127.0.0.1", "--port", "9000", "--base-path", "/qd"])
        self.assertEqual(service["Restart"], "on-failure")
        self.assertEqual(service.getint("RestartSec"), 5)
        self.assertEqual(shlex.split(service["Environment"]), [f"XDG_STATE_HOME={self.root / 'state'}"])
        self.assertEqual(unit["Install"]["WantedBy"], "default.target")

    def test_url_reads_the_installed_unit(self) -> None:
        self.assertIn("http://127.0.0.1:8765/", self.run_cli("url")[1])
        unit = self.root / "config" / "systemd" / "user" / "quarterdeck.service"
        unit.parent.mkdir(parents=True)
        unit.write_text('[Service]\nExecStart="python3" "quarterdeck.py" "serve" "--host" "192.0.2.10" "--port" "9000" '
                        '"--base-path" "/qd" "--tls-cert" "c.pem" "--tls-key" "k.pem"\n')
        self.assertEqual(json.loads(self.run_cli("url", "--json")[1])["urls"], ["https://192.0.2.10:9000/qd/"])

    def test_help_lists_the_item_commands_and_existing_docs(self) -> None:
        code, out, _err = self.run_cli("help", "--json")
        self.assertEqual(code, 0)
        index = json.loads(out)
        names = [command["name"] for command in index["commands"]]
        for name in ("add", "list", "show", "remove", "render", "serve"):
            self.assertIn(name, names)
        for old in ("board", "reports", "topics", "waits", "attention", "export", "edit", "done", "reopen"):
            self.assertNotIn(old, names)
        for command in index["commands"]:
            for doc in command["docs"]:
                path = ROOT / doc if doc == "README.md" else ROOT / "docs" / doc
                self.assertTrue(path.is_file(), f"{command['name']} names a missing doc: {doc}")
        self.assertIn("never reused", quarterdeck.make_parser().format_help())

    def test_the_command_runs_as_a_script(self) -> None:
        result = subprocess.run([sys.executable, str(ROOT / "quarterdeck.py"), "list"], capture_output=True, text=True,
                                env=dict(os.environ), check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No saved items.", result.stdout)


def tiny_png(color: tuple[int, int, int], size: int = 4, *, comment: bytes | None = None) -> bytes:
    """A small invented PNG: one flat colour."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    rows = b"".join(b"\x00" + bytes(color) * size for _ in range(size))
    text = chunk(b"tEXt", b"Comment\0" + comment) if comment is not None else b""
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + text + chunk(b"IEND", b""))


class ImageCase(StoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.pictures = self.root / "pictures"
        self.pictures.mkdir()
        self.red = self.picture("red.png", tiny_png((200, 40, 40)))
        self.blue = self.picture("blue.png", tiny_png((40, 40, 200)))
        self.wide = self.picture("wide.png", tiny_png((40, 160, 40), 8))

    def picture(self, name: str, data: bytes) -> Path:
        path = self.pictures / name
        path.write_bytes(data)
        return path

    def assert_store_locked(self) -> None:
        lock_path = quarterdeck.store_path().with_name("items.json.lock")
        with lock_path.open("r+") as lock, self.assertRaises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def pictured(self) -> list[dict]:
        return [{"question": "Which colour for the quay?", "context": "Both fit the quay.",
                 "images": [{"path": str(self.wide), "alt": "Both colours side by side"}],
                 "options": [{"label": "Red", "image": {"path": str(self.red), "alt": "A red quay"}},
                             {"label": "Blue", "image": {"path": str(self.blue), "alt": "A blue quay"}}, "Neither"],
                 "suggestion": "Red"}]

    def add_pictured(self, questions: list[dict] | None = None) -> str:
        return self.add("questions", "Quay colours", "Saved to pick a colour.", "--body-file", "-",
                        stdin=json.dumps(questions or self.pictured()))


class QuestionImageTests(ImageCase):
    def test_images_are_copied_into_the_private_store(self) -> None:
        png = tiny_png((200, 40, 40))
        self.red = self.picture("red.jpg", png)
        expected_name = hashlib.sha256(png).hexdigest()[:16] + ".png"
        item_id = self.add_pictured()
        stored = self.store()["items"][0]["body"]["questions"][0]
        self.assertNotIn("path", json.dumps(stored))
        self.assertEqual(stored["options"][2], "Neither")
        image = stored["options"][0]["image"]
        self.assertEqual(image, {"file": expected_name, "alt": "A red quay"})
        folder = quarterdeck.store_path().parent / "images" / item_id.lower()
        self.assertEqual((folder / expected_name).read_bytes(), png)
        self.assertEqual(stat.S_IMODE((folder / expected_name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_import_preserves_whitespace_and_snapshots_repeated_image_paths(self) -> None:
        questions = self.pictured()
        self.picture("mockup A.png", tiny_png((0, 0, 0)))
        expected = tiny_png((255, 255, 255))
        exact = self.picture("mockup  A.png", expected)
        replacement = tiny_png((120, 30, 90))
        next_picture = self.picture("next.png", replacement)
        questions[0]["images"][0]["path"] = f"{exact.parent}/./{exact.name}"
        questions[0]["images"].append({
            "path": f"{exact.parent}/../{exact.parent.name}/{exact.name}",
            "alt": "Another view of the quay",
        })
        questions[0]["options"][0]["image"]["path"] = str(exact)
        open_file = open

        def open_and_replace(path, *args, **kwargs):
            stream = open_file(path, *args, **kwargs)
            if path == str(exact) and next_picture.exists():
                next_picture.replace(exact)
            return stream

        with patch("builtins.open", side_effect=open_and_replace):
            item_id = self.add_pictured(questions)
        saved = self.store()["items"][0]["body"]["questions"][0]
        gallery, alternate = saved["images"]
        option = saved["options"][0]["image"]
        self.assertEqual({gallery["file"], alternate["file"], option["file"]}, {gallery["file"]})
        self.assertEqual((gallery["alt"], alternate["alt"], option["alt"]),
                         ("Both colours side by side", "Another view of the quay", "A red quay"))
        root = quarterdeck.store_path().parent / "images"
        self.assertEqual((root / item_id.lower() / gallery["file"]).read_bytes(), expected)

        next_id = self.add_pictured(questions)
        next_image = self.store()["items"][1]["body"]["questions"][0]["images"][0]
        self.assertEqual((root / next_id.lower() / next_image["file"]).read_bytes(), replacement)

    def test_image_path_cache_preserves_parent_traversal_through_symlinks(self) -> None:
        direct = self.picture("mockup.png", tiny_png((10, 20, 30)))
        nested = self.pictures / "nested" / "child"
        nested.mkdir(parents=True)
        expected = tiny_png((30, 20, 10))
        (nested.parent / direct.name).write_bytes(expected)
        alias = self.pictures / "alias"
        alias.symlink_to(nested, target_is_directory=True)
        questions = self.pictured()
        questions[0]["options"][0]["image"]["path"] = str(direct)
        questions[0]["images"][0]["path"] = str(alias / ".." / direct.name)
        item_id = self.add_pictured(questions)
        gallery = self.store()["items"][0]["body"]["questions"][0]["images"][0]
        self.assertEqual((quarterdeck.store_path().parent / "images" /
                          item_id.lower() / gallery["file"]).read_bytes(), expected)

    def test_plain_text_options_keep_working_unchanged(self) -> None:
        self.add_all_kinds()
        self.assertEqual(self.store()["items"][2]["body"]["questions"][0]["options"], ["Merge", "Hold"])
        self.assertFalse((quarterdeck.store_path().parent / "images").exists())

    def test_unsuitable_images_are_refused_and_nothing_is_saved(self) -> None:
        gif = self.picture("pic.gif", b"GIF89a" + bytes(20))
        svg = self.picture("pic.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        renamed = self.picture("fake.png", b"not an image at all")
        empty_comment = tiny_png((1, 2, 3), comment=b"")
        comment = b"x" * (5_000_000 + 1 - len(empty_comment))
        big = self.picture("big.png", tiny_png((1, 2, 3), comment=comment))
        cases = {
            "gif": ({"path": str(gif), "alt": "x"}, "must be a PNG, JPEG or still WebP image"),
            "svg": ({"path": str(svg), "alt": "x"}, "must be a PNG, JPEG or still WebP image"),
            "renamed": ({"path": str(renamed), "alt": "x"}, "must be a PNG, JPEG or still WebP image"),
            "big": ({"path": str(big), "alt": "x"}, "larger than 5 MB"),
            "missing": ({"path": str(self.pictures / "none.png"), "alt": "x"}, "could not be read"),
            "no alt": ({"path": str(self.red)}, "needs alt text"),
            "blank alt": ({"path": str(self.red), "alt": "  "}, "alt text must not be empty"),
            "no path": ({"alt": "x"}, "path must be text"),
            "extra field": ({"path": str(self.red), "alt": "x", "url": "https://example.com/a.png"}, "unsupported field"),
            "stored field": ({"file": "0123456789abcdef.png", "alt": "x"}, "unsupported field"),
        }
        for name, (image, message) in cases.items():
            with self.subTest(name):
                question = {"question": "Which?", "context": "Pick one.", "options": ["A"], "images": [image]}
                code, _out, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                               "--body-file", "-", stdin=json.dumps([question]))
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        self.assertEqual(self.store()["items"] if quarterdeck.store_path().exists() else [], [])
        self.assertFalse((quarterdeck.store_path().parent / "images").exists())

    def test_named_pipe_is_refused_before_opening_in_either_attachment_type(self) -> None:
        pipe = self.pictures / "mockup.png"
        os.mkfifo(pipe)
        image = {"path": str(pipe), "alt": "A mockup"}
        open_file = open

        def refuse_pipe_open(path, *args, **kwargs):
            if str(path) == str(pipe):
                self.fail("add attempted to open a named pipe")
            return open_file(path, *args, **kwargs)

        for fields in ({"options": ["A"], "images": [image]}, {"options": [{"label": "A", "image": image}]}):
            with self.subTest(fields=fields), patch("builtins.open", side_effect=refuse_pipe_open):
                question = {"question": "Which?", "context": "Pick one.", **fields}
                code, _, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                            "--body-file", "-", stdin=json.dumps([question]))
                self.assertEqual(code, 2)
                self.assertIn("must be a regular file", err)
                self.assertIn(pipe.name, err)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_attachment_replaced_by_a_named_pipe_is_refused_without_blocking(self) -> None:
        child = """
import builtins
import os
import sys
import quarterdeck

fixture = sys.argv[1]
real_open = builtins.open

def replace_before_open(path, *args, **kwargs):
    if path == fixture:
        os.unlink(fixture)
        os.mkfifo(fixture)
    return real_open(path, *args, **kwargs)

builtins.open = replace_before_open
sys.exit(quarterdeck.main(["add", "--kind", "questions", "--title", "T",
                          "--description", "D", "--body-file", "-"]))
"""
        for field in ("images", "options"):
            with self.subTest(field=field):
                fixture = self.picture(f"{field}.png", tiny_png((1, 2, 3)))
                image = {"path": str(fixture), "alt": "A mockup"}
                question = {"question": "Which?", "context": "Pick one.", "options": ["One"]}
                question[field] = [image] if field == "images" else [{"label": "One", "image": image}]
                result = subprocess.run([sys.executable, "-c", child, str(fixture)], cwd=ROOT,
                                        input=json.dumps([question]), text=True, capture_output=True, timeout=3)
                self.assertTrue(stat.S_ISFIFO(fixture.stat().st_mode))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("must be a regular file", result.stderr)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_truncated_images_are_refused_in_either_attachment_type(self) -> None:
        for filename, content in (
            ("short.png", tiny_png((1, 2, 3))[:-12]),
            ("short.jpg", b"\xff\xd8\xff\xd9"),
            ("short.webp", b"RIFF\x04\x00\x00\x00WEBP"),
        ):
            image = {"path": str(self.picture(filename, content)), "alt": "A mockup"}
            for field, value in (("images", [image]), ("options", [{"label": "One", "image": image}])):
                with self.subTest(filename=filename, field=field):
                    question = {"question": "Which?", "context": "Pick one.", "options": ["One"], field: value}
                    code, _, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                                "--body-file", "-", stdin=json.dumps([question]))
                    self.assertEqual(code, 2)
                    self.assertIn("must be a PNG, JPEG or still WebP image", err)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_too_many_images_and_malformed_options_are_refused(self) -> None:
        many = [{"path": str(self.red), "alt": "A red quay"}] * (quarterdeck.IMAGE_COUNT_LIMIT + 1)
        for question, message in (
            ({"question": "Q", "context": "C", "options": ["A"], "images": many}, "at most 24 images"),
            ({"question": "Q", "context": "C", "options": [{"label": "A", "colour": "red"}]}, "unsupported field"),
            ({"question": "Q", "context": "C", "options": [{"label": "A"}]}, "image must be an object"),
            ({"question": "Q", "context": "C", "options": [{"label": "A", "image": None}]}, "image must be an object"),
            ({"question": "Q", "context": "C", "options": [{"image": {"path": str(self.red), "alt": "x"}}]}, "label"),
            ({"question": "Q", "context": "C", "options": ["A"], "images": "red.png"}, "images must be a list"),
        ):
            with self.subTest(message):
                code, _out, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                               "--body-file", "-", stdin=json.dumps([question]))
                self.assertEqual(code, 2)
                self.assertIn(message, err)

    def test_mixed_attachment_limit_is_checked_before_opening_paths(self) -> None:
        missing = {"path": str(self.pictures / "missing.png"), "alt": "A mockup"}
        questions = [
            {"question": "Q1", "context": "C", "options": ["A"], "images": [missing] * 24},
            {"question": "Q2", "context": "C", "options": [{"label": "A", "image": missing}]},
        ]
        code, _, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                    "--body-file", "-", stdin=json.dumps(questions))
        self.assertEqual(code, 2)
        self.assertIn("at most 24 images", err)

    def test_pictured_choices_cannot_share_a_normalized_label(self) -> None:
        pictured = {"label": " A  card ", "image": {"path": str(self.red), "alt": "A red quay"}}
        other = {"label": "A card", "image": {"path": str(self.blue), "alt": "A blue quay"}}
        for options in ([pictured, other], [pictured, "A card"], ["A card", pictured]):
            with self.subTest(options=options):
                question = {"question": "Which?", "context": "Pick one.", "options": options}
                code, _, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                            "--body-file", "-", stdin=json.dumps([question]))
                self.assertEqual(code, 2)
                self.assertIn("duplicates", err)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_an_interrupt_after_commit_preserves_saved_images(self) -> None:
        replace = os.replace

        def interrupt_after_replace(source, destination):
            replace(source, destination)
            if destination == quarterdeck.store_path():
                raise KeyboardInterrupt

        with patch.object(os, "replace", side_effect=interrupt_after_replace), self.assertRaises(KeyboardInterrupt):
            self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                         "--body-file", "-", stdin=json.dumps(self.pictured()))

        self.assertEqual(self.store()["items"][0]["id"], "Q1")
        code, _, err = self.run_cli("render")
        self.assertEqual(code, 0, err)
        folder = quarterdeck.store_path().parent / "images" / "q1"
        self.assertEqual({path.read_bytes() for path in folder.iterdir()},
                         {tiny_png((200, 40, 40)), tiny_png((40, 40, 200)), tiny_png((40, 160, 40), 8)})

    def test_remove_deletes_the_images_with_the_item(self) -> None:
        keep = self.add_pictured()
        gone = self.add_pictured()
        folder = quarterdeck.store_path().parent / "images"
        self.assertEqual({path.name for path in folder.iterdir()}, {keep.lower(), gone.lower()})
        self.assertEqual(self.run_cli("remove", gone)[0], 0)
        self.assertEqual(list((folder / gone.lower()).iterdir()), [])
        self.assertEqual(len(list((folder / keep.lower()).iterdir())), 3)

    def test_a_stored_image_record_that_is_not_canonical_is_reported_as_damage(self) -> None:
        self.add_pictured()
        data = self.store()
        data["items"][0]["body"]["questions"][0]["images"][0]["file"] = "../../items.json"
        quarterdeck.store_path().write_text(json.dumps(data))
        code, _out, err = self.run_cli("list")
        self.assertEqual(code, 2)
        self.assertIn("damaged", err)

    def test_help_mentions_images(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            quarterdeck.main(["add", "--help"])
        self.assertIn("image", out.getvalue())
        self.assertIn("PNG, JPEG or still WebP", " ".join(out.getvalue().split()))


class QuestionImagePageTests(ImageCase):
    @contextmanager
    def observe_image_reads(self):
        image_root = quarterdeck.store_path().parent.resolve() / "images"
        open_path = Path.open
        reads = {}

        def observe_open(path, *args, **kwargs):
            stream = open_path(path, *args, **kwargs)
            if image_root not in path.resolve().parents:
                return stream
            reads.setdefault(path, 0)
            observed = MagicMock(wraps=stream)
            observed.__enter__.return_value = observed
            observed.__exit__.side_effect = stream.__exit__

            def read(*args, **kwargs):
                reads[path] += 1
                return stream.read(*args, **kwargs)

            observed.read.side_effect = read
            return observed

        with patch.object(Path, "open", autospec=True, side_effect=observe_open):
            yield reads

    def start(self, base_path: str = "") -> int:
        server = quarterdeck.make_server("127.0.0.1", 0, quarterdeck.make_handler("Quarterdeck", base_path))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def fetch(self, port: int, path: str) -> tuple[int, bytes, dict]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def test_served_question_shows_images_with_alt_text_on_the_question_and_each_option(self) -> None:
        item_id = self.add_pictured()
        port = self.start()
        page_url = f"/{item_id.lower()}.html"
        status, body, _headers = self.fetch(port, page_url)
        page = body.decode()
        self.assertEqual(status, 200)
        wide = self.store()["items"][0]["body"]["questions"][0]["images"][0]["file"]
        self.assertIn(f'<img src="images/{item_id.lower()}/{wide}" alt="Both colours side by side"', page)
        self.assertEqual(page.count("<img "), 3)
        pictured = re.findall(r'<label\b[^>]*>(<img\b[^>]*>.*?)</label>', page, re.DOTALL)
        self.assertEqual(len(pictured), 2)
        for label, alt, expected in (("Red", "A red quay", tiny_png((200, 40, 40))),
                                     ("Blue", "A blue quay", tiny_png((40, 40, 200)))):
            with self.subTest(label=label):
                choice, = (markup for markup in pictured if f'data-choice="{label}"' in markup)
                source, mockup_id = re.search(r'<img\b[^>]*\bsrc="([^"]+)"[^>]*\bid="([^"]+)"', choice).groups()
                self.assertIn(f'alt="{alt}"', choice)
                self.assertEqual(self.fetch(port, urljoin(page_url, source))[:2], (200, expected))
                name_id, description = re.search(
                    r'<input type="radio"[^>]*\baria-labelledby="([^"]+)"[^>]*\baria-describedby="([^"]+)"', choice,
                ).groups()
                self.assertIn(f'<span id="{name_id}">{label}</span>', choice)
                description_ids = description.split()
                self.assertEqual(description_ids[0], mockup_id)
                if label == "Red":
                    self.assertEqual(len(description_ids), 2)
                    self.assertIn(f'<small id="{description_ids[1]}">Suggested</small>', choice)
                else:
                    self.assertEqual(description_ids, [mockup_id])
        self.assertIn('aria-label="Open image for Red">Open image</a>', page)
        self.assertIn('<label class="choice"><input type="radio" name="choice-Q1.1" data-choice="Neither"> Neither</label>', page)

    def assert_read_only_option_images(self, page, read_image) -> None:
        options = re.findall(
            r'<li class="option"><a\b[^>]*\bhref="([^"]+)"[^>]*><img\b[^>]*\bsrc="([^"]+)"[^>]*></a>'
            r'<span>([^<]+)</span></li>', page,
        )
        self.assertEqual([label for _, _, label in options], ["Red", "Blue"])
        expected = {"Red": tiny_png((200, 40, 40)), "Blue": tiny_png((40, 40, 200))}
        for link, source, label in options:
            with self.subTest(label=label):
                self.assertEqual(link, source)
                self.assertEqual(read_image(source), expected[label])

    def test_read_only_options_link_their_images_and_text_is_escaped(self) -> None:
        questions = self.pictured()
        questions[0]["options"][0]["image"]["alt"] = '<b onclick="x">"red"</b>'
        item_id = self.add_pictured(questions)
        self.run_cli("mark", item_id, "closed")
        port = self.start()
        page_url = f"/{item_id.lower()}.html"
        status, body, _headers = self.fetch(port, page_url)
        self.assertEqual(status, 200)
        page = body.decode()
        self.assertNotIn("<b onclick", page)
        self.assertIn("&lt;b onclick=&quot;x&quot;&gt;", page)
        self.assertRegex(page, r'<li class="option"><a class="shot" href="images/q1/[0-9a-f]{16}[.]png" target="_blank" rel="noopener"><img ')

        def read_image(source):
            status, content, _headers = self.fetch(port, urljoin(page_url, source))
            self.assertEqual(status, 200)
            return content

        self.assert_read_only_option_images(page, read_image)

    def test_images_are_served_only_from_the_store_and_only_for_their_item(self) -> None:
        first = self.add_pictured()
        second = self.add("questions", "Plain", "No images.", "--body-file", "-", stdin=json.dumps(QUESTIONS))
        unlisted = tiny_png((1, 2, 3))
        unlisted_name = hashlib.sha256(unlisted).hexdigest()[:16] + ".png"
        (quarterdeck.store_path().parent / "images" / first.lower() / unlisted_name).write_bytes(unlisted)
        for original in (self.red, self.blue, self.wide):
            original.unlink()
        port = self.start()
        question = self.store()["items"][0]["body"]["questions"][0]
        image = question["options"][0]["image"]["file"]
        for file, expected in ((image, tiny_png((200, 40, 40))),
                               (question["images"][0]["file"], tiny_png((40, 160, 40), 8))):
            status, body, headers = self.fetch(port, f"/images/{first.lower()}/{file}")
            self.assertEqual((status, body, headers["Content-Type"]), (200, expected, "image/png"))
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertNotIn("Content-Encoding", headers)
        for path in (f"/images/{second.lower()}/{image}", f"/images/{first.lower()}/{unlisted_name}",
                     f"/images/{first.lower()}/..%2Fitems.json", f"/images/{first.lower()}/%2e%2e/items.json",
                     "/images/q1/", "/images/q1", "/images/q9/" + image, f"/images/{first.upper()}/{image}",
                     "/items.json", f"/images/{first.lower()}/{image[:-4]}.gif"):
            with self.subTest(path=path):
                self.assertEqual(self.fetch(port, path)[0], 404)
        self.run_cli("remove", first)
        self.assertEqual(self.fetch(port, f"/images/{first.lower()}/{image}")[0], 404)

    def test_images_are_served_under_a_base_path_and_the_page_links_stay_relative(self) -> None:
        item_id = self.add_pictured()
        port = self.start("/qd")
        page = self.fetch(port, f"/qd/{item_id.lower()}.html")[1].decode()
        source = re.search(r'<img src="([^"]+)"', page).group(1)
        self.assertFalse(source.startswith("/"))
        self.assertEqual(self.fetch(port, "/qd/" + source)[0], 200)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/" + source)
        response = connection.getresponse()
        self.assertEqual((response.status, response.getheader("Location")), (308, "/qd/" + source))
        connection.close()

    def images_of(self, state: Path) -> dict[str, bytes]:
        folder = state / "quarterdeck" / "images"
        return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}

    def test_render_from_another_store_never_deletes_this_stores_images(self) -> None:
        replacement = self.picture("replacement.png", tiny_png((9, 9, 9)))
        damage = {"missing": None, "empty object": "{}", "no next_id": '{"version": 1, "items": []}',
                  "no version": '{"next_id": 1, "items": []}', "damaged": "{not json", "empty file": ""}
        other = {"empty": [], "same id, other content": ["pictured"], "same id, text only": ["text"]}
        for name, text in damage.items():
            for shape, items in other.items():
                with self.subTest(store=name, other=shape), tempfile.TemporaryDirectory() as temp:
                    first, second = Path(temp) / "first", Path(temp) / "second"
                    with patch.dict(os.environ, {"XDG_STATE_HOME": str(first)}):
                        self.add_pictured()
                        store = quarterdeck.store_path()
                        code, _, err = self.run_cli("render")
                        self.assertEqual(code, 0, err)
                    item_page = store.parent / "q1.html"
                    self.assertIn("<h1>Quay colours</h1>", item_page.read_text())
                    before = self.images_of(first)
                    self.assertEqual(len(before), 3)
                    if text is None:
                        store.unlink()
                    else:
                        store.write_text(text)
                    with patch.dict(os.environ, {"XDG_STATE_HOME": str(second)}):
                        for kind in items:
                            if kind == "pictured":
                                questions = self.pictured()
                                questions[0]["options"][0]["image"]["path"] = str(replacement)
                                self.add("questions", "New quay colours", "A replacement mockup.",
                                         "--body-file", "-", stdin=json.dumps(questions))
                            else:
                                self.add("questions", "Plain", "No images.", "--body-file", "-", stdin=json.dumps(QUESTIONS))
                        code, _, err = self.run_cli("render", "--output", str(store.parent / "index.html"))
                        self.assertEqual(code, 0, err)
                    if items:
                        title = "New quay colours" if items == ["pictured"] else "Plain"
                        self.assertIn(f"<h1>{title}</h1>", item_page.read_text())
                    else:
                        self.assertFalse(item_page.exists())
                    self.assertEqual({name: data for name, data in self.images_of(first).items()
                                      if name in before}, before)

    def test_failed_item_page_write_preserves_the_previous_saved_page(self) -> None:
        self.add_pictured()
        default = self.root / "default" / "index.html"
        custom = self.root / "custom" / "Shelf.html"
        config = self.root / "config.json"
        config.write_text(json.dumps({"output_dir": str(default.parent)}))
        destinations = ((default, ("--config", str(config))), (custom, ("--output", str(custom))))
        for output, args in destinations:
            code, _, err = self.run_cli("render", *args)
            self.assertEqual(code, 0, err)
        previous = {output: output.read_bytes() for output, _ in destinations}
        self.add_pictured()
        write_private = quarterdeck.write_private

        def fail_q2(path, content):
            if path.name == "q2.html":
                raise OSError("disk full")
            write_private(path, content)

        for output, args in destinations:
            with self.subTest(output=output), patch.object(quarterdeck, "write_private", side_effect=fail_q2):
                code, _, err = self.run_cli("render", *args)
                self.assertEqual(code, 2)
                self.assertIn("disk full", err)
                self.assertFalse((output.parent / "q2.html").exists())
                self.assertEqual(output.read_bytes(), previous[output])

    def test_external_render_can_read_a_read_only_store(self) -> None:
        self.add_pictured()
        store = quarterdeck.store_path()
        lock = store.with_name("items.json.lock")
        originals = self.images_of(store.parent.parent)
        self.assertEqual(len(originals), 3)
        explicit = self.root / "explicit"
        configured = self.root / "configured"
        config = self.root / "config.json"
        config.write_text(json.dumps({"output_dir": str(configured)}))
        chmod = os.chmod
        os_open = os.open
        path_open = Path.open

        def deny_store_chmod(path, *args, **kwargs):
            if Path(path) == store.parent:
                raise OSError(errno.EROFS, "Read-only file system")
            return chmod(path, *args, **kwargs)

        def deny_store_lock_writes(path, flags, *args, **kwargs):
            if Path(path) == lock and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
                raise OSError(errno.EROFS, "Read-only file system")
            return os_open(path, flags, *args, **kwargs)

        def open_backup_file(path, *args, **kwargs):
            if path == lock and lock_access == "denied":
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return path_open(path, *args, **kwargs)

        with (patch.object(os, "chmod", side_effect=deny_store_chmod),
              patch.object(os, "open", side_effect=deny_store_lock_writes),
              patch.object(Path, "open", autospec=True, side_effect=open_backup_file)):
            for directory, args, lock_access in (
                (explicit, ("--output", str(explicit / "index.html")), "readable"),
                (explicit, ("--output", str(explicit / "index.html")), "denied"),
                (configured, ("--config", str(config)), "denied"),
                (configured, ("--config", str(config)), "absent"),
            ):
                with self.subTest(directory=directory, lock_access=lock_access):
                    if lock_access == "absent":
                        lock.unlink()
                    code, _, err = self.run_cli("render", *args)
                    self.assertEqual(code, 0, err)
                    self.assertEqual(lock.exists(), lock_access != "absent")
                    self.assertTrue((directory / "q1.html").is_file())
                    for name, data in originals.items():
                        self.assertEqual((directory / "images" / name).read_bytes(), data)

    def test_add_and_render_write_images_without_hard_link_support(self) -> None:
        expected = {self.red.read_bytes(), self.blue.read_bytes(), self.wide.read_bytes()}
        output = self.root / "site" / "index.html"
        staged = {}
        os_open = os.open

        def link_unsupported(temporary, target):
            staged[target] = Path(temporary)
            raise OSError(errno.EOPNOTSUPP, "Hard links are unsupported")

        def open_with_space_for_one_copy(path, flags, *args, **kwargs):
            temporary = staged.get(Path(path))
            if flags & os.O_EXCL and temporary is not None and temporary.exists():
                raise OSError(errno.ENOSPC, "Only one image copy fits")
            return os_open(path, flags, *args, **kwargs)

        with (patch.object(os, "link", side_effect=link_unsupported),
              patch.object(os, "open", side_effect=open_with_space_for_one_copy)):
            self.assertEqual(self.add_pictured(), "Q1")
            code, _, err = self.run_cli("render", "--output", str(output))
            self.assertEqual(code, 0, err)
        for directory in (quarterdeck.store_path().parent, output.parent):
            copies = list((directory / "images" / "q1").iterdir())
            self.assertEqual({copy.read_bytes() for copy in copies}, expected)
            self.assertEqual([stat.S_IMODE(copy.stat().st_mode) for copy in copies], [0o600] * 3)

    def test_render_without_hard_links_preserves_an_image_created_during_export(self) -> None:
        self.add_pictured()
        png = self.red.read_bytes()
        filename = hashlib.sha256(png).hexdigest()[:16] + ".png"
        for occupant, expected_code in ((png, 0), (b"other bytes", 2)):
            output = self.root / f"site-{expected_code}" / "index.html"
            image = output.parent / "images" / "q1" / filename

            def link_unsupported(_temporary, target):
                if target == image:
                    image.write_bytes(occupant)
                raise OSError(errno.EOPNOTSUPP, "Hard links are unsupported")

            with self.subTest(expected_code=expected_code), patch.object(os, "link", side_effect=link_unsupported):
                code, _, err = self.run_cli("render", "--output", str(output))
                self.assertEqual(code, expected_code, err)
                self.assertEqual(image.read_bytes(), occupant)
                self.assertEqual(output.exists(), expected_code == 0)
                if expected_code:
                    self.assertIn("refusing to overwrite an unrelated file", err)

    def test_remove_deletes_exactly_the_files_the_record_names(self) -> None:
        keep, gone = self.add_pictured(), self.add_pictured()
        images = quarterdeck.store_path().parent / "images"
        unlisted = tiny_png((1, 2, 3))
        unlisted_name = hashlib.sha256(unlisted).hexdigest()[:16] + ".png"
        removed_folder = images / gone.lower()
        (removed_folder / unlisted_name).write_bytes(unlisted)
        self.assertEqual(self.run_cli("remove", gone)[0], 0)
        self.assertEqual({path.name: path.read_bytes() for path in removed_folder.iterdir()},
                         {unlisted_name: unlisted})
        self.assertEqual(len(list((images / keep.lower()).iterdir())), 3)
        self.assertEqual(self.run_cli("remove", keep)[0], 0)
        self.assertEqual(list((images / keep.lower()).iterdir()), [])

    def test_a_failed_add_deletes_nothing_and_the_retry_succeeds(self) -> None:
        images = quarterdeck.store_path().parent / "images" / "q1"
        images.mkdir(parents=True)
        stray = images / "stray.txt"
        stray.write_text("kept")
        with patch.object(quarterdeck, "write_store", side_effect=OSError("disk full")):
            code, _out, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                           "--body-file", "-", stdin=json.dumps(self.pictured()))
        self.assertEqual(code, 2)
        self.assertIn("disk full", err)
        self.assertEqual(stray.read_text(), "kept")
        self.assertCountEqual(
            [path.read_bytes() for path in images.iterdir() if path != stray],
            [picture.read_bytes() for picture in (self.red, self.blue, self.wide)],
        )
        self.assertEqual(self.add_pictured(), "Q1")
        self.assertEqual(len(list(images.iterdir())), 4)

    def test_a_retargeted_symlink_is_resolved_once_for_every_reference(self) -> None:
        link = self.pictures / "link.png"
        link.symlink_to(self.red)
        real = os.path.realpath

        def retargeting(path, *args, **kwargs):
            result = real(path, *args, **kwargs)
            if Path(path) == link:
                link.unlink()
                link.symlink_to(self.blue)
            return result

        question = {"question": "Which?", "context": "Pick one.", "options": [
            {"label": "A", "image": {"path": str(link), "alt": "First"}},
            {"label": "B", "image": {"path": str(link), "alt": "Second"}}]}
        with patch.object(os.path, "realpath", side_effect=retargeting):
            self.add_pictured([question])
        self.assertEqual(link.readlink(), self.blue)
        options = self.store()["items"][0]["body"]["questions"][0]["options"]
        self.assertEqual(options[0]["image"]["file"], options[1]["image"]["file"])
        folder = quarterdeck.store_path().parent / "images" / "q1"
        self.assertEqual((folder / options[0]["image"]["file"]).read_bytes(), tiny_png((200, 40, 40)))

    def test_no_file_is_opened_until_the_whole_body_is_valid(self) -> None:
        opened: list[str] = []
        real_open = open

        def recording(file, *args, **kwargs):
            if str(file).startswith(str(self.pictures)):
                opened.append(str(file))
            return real_open(file, *args, **kwargs)

        questions = self.pictured() + [{"question": "No context", "options": ["A"]}]
        with patch("builtins.open", side_effect=recording):
            code, _out, err = self.run_cli("add", "--kind", "questions", "--title", "T", "--description", "D",
                                           "--body-file", "-", stdin=json.dumps(questions))
        self.assertEqual(code, 2)
        self.assertIn("context", err)
        self.assertEqual(opened, [])

    def test_render_deletes_no_image_in_an_export(self) -> None:
        keep, gone = self.add_pictured(), self.add_pictured()
        output = self.root / "site" / "index.html"
        self.assertEqual(self.run_cli("render", "--output", str(output))[0], 0)
        exported = {path for path in (output.parent / "images").rglob("*") if path.is_file()}
        self.assertEqual(len(exported), 6)
        self.assertEqual(self.run_cli("remove", gone)[0], 0)
        self.assertEqual(self.run_cli("render", "--output", str(output))[0], 0)
        self.assertFalse((output.parent / f"{gone.lower()}.html").exists())
        self.assertTrue((output.parent / f"{keep.lower()}.html").exists())
        self.assertEqual({path for path in (output.parent / "images").rglob("*") if path.is_file()}, exported)

    def test_render_reserves_images_even_before_the_first_pictured_add(self) -> None:
        for directory in (quarterdeck.store_path().parent, self.root / "site"):
            output = directory / "images"
            code, _, err = self.run_cli("render", "--output", str(output))
            self.assertEqual(code, 2)
            self.assertIn("reserved for images", err)
            self.assertFalse(output.exists())
        self.assertEqual(self.add_pictured(), "Q1")

    def test_live_render_reads_its_snapshot_under_the_store_lock(self) -> None:
        self.add_pictured()
        state = quarterdeck.store_path().parent
        alias = self.root / "state-alias"
        alias.symlink_to(state, target_is_directory=True)
        read_store = quarterdeck.read_store
        def read_snapshot(path):
            self.assert_store_locked()
            return read_store(path)

        for args in ((), ("--output", str(alias / "index.html"))):
            with (self.subTest(args=args), self.observe_image_reads() as reads,
                  patch.object(quarterdeck, "read_store", side_effect=read_snapshot)):
                code, _, err = self.run_cli("render", *args)
                self.assertEqual(code, 0, err)
                self.assertEqual(sorted(reads.values()), [0] * 3)

    def test_render_into_the_state_folder_keeps_the_stores_own_images(self) -> None:
        item_id = self.add_pictured()
        self.assertEqual(self.run_cli("render")[0], 0)
        folder = quarterdeck.store_path().parent / "images" / item_id.lower()
        self.assertEqual(len(list(folder.iterdir())), 3)
        self.assertEqual(self.run_cli("render")[0], 0)
        self.assertEqual(len(list(folder.iterdir())), 3)

    def test_render_reports_a_missing_stored_image(self) -> None:
        item_id = self.add_pictured()
        shutil.rmtree(quarterdeck.store_path().parent / "images" / item_id.lower())
        for args in ((), ("--output", str(self.root / "site" / "index.html"))):
            with self.subTest(args=args):
                code, _out, err = self.run_cli("render", *args)
                self.assertEqual(code, 2)
                self.assertIn("missing from the store", err)

    def test_the_privacy_guard_still_rejects_image_files(self) -> None:
        self.assertIn("image", privacy_guard.forbidden_path("images/q1/0123456789abcdef.png"))


class PrivacyGuardTests(unittest.TestCase):
    def initialize_git(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    def stage(self, root: Path, name: str, content: str = "fictional example\n") -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "--", name], cwd=root, check=True)

    def test_rejects_staged_generated_output_and_real_data_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.initialize_git(root)
            self.stage(root, "quarterdeck-output/index.html")
            self.assertTrue(any("generated output" in item for item in privacy_guard.check_staged(root)))

    def test_item_store_is_ignored_and_force_staging_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.initialize_git(root)
            (root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
            for name in ("quarterdeck/items.json", "nested/quarterdeck/items.json", "nested/quarterdeck/items.json.lock"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Fictional decision context.")
                subprocess.run(["git", "check-ignore", "--", name], cwd=root, check=True, capture_output=True)
                subprocess.run(["git", "add", "-f", "--", name], cwd=root, check=True)
                self.assertIn(f"{name}: local Quarterdeck item store or lock", privacy_guard.check_staged(root))

    def test_rejects_generated_page_marker_in_an_unusual_filename(self) -> None:
        marker = b'<meta name="quarterdeck-' + b'generated"'
        self.assertIn("Quarterdeck-generated HTML marker", privacy_guard.content_findings(marker))

    def test_rejects_host_specific_content_but_accepts_fictional_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.initialize_git(root)
            self.stage(root, "README.md", "Example path: " + "/" + "home/unknown-user/private-work\n")
            self.assertTrue(any("host-specific" in item for item in privacy_guard.check_staged(root)))
            subprocess.run(["git", "reset", "-q"], cwd=root, check=True)
            self.stage(root, "README.md", "Example project: Maple Harbor inventory\n")
            self.assertEqual(privacy_guard.check_staged(root), [])

    def test_rejects_ticket_shaped_work_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.initialize_git(root)
            work_key = "FABLE" + "-918"
            self.stage(root, "README.md", "Example task key: " + work_key + "\n")
            self.assertTrue(any("ticket-shaped" in item for item in privacy_guard.check_staged(root)))


if __name__ == "__main__":
    unittest.main()
