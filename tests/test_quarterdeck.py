from __future__ import annotations

import base64
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
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from contextlib import contextmanager, redirect_stderr, redirect_stdout, suppress
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
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
        for gone in ("My desk", "Needs attention", "Reviewed questions"):
            self.assertNotIn(gone, index)
        self.assertLess(index.index("<h1>Desk</h1>"), index.index("<h2>References</h2>"))
        self.assertLess(index.index(">Q2<"), index.index(">Q1<"))
        self.assertIn("Harbor chores", index)
        self.assertIn('<a class="card-link" href="q3.html#Q3.1"><span class="id">Q3</span> Tide launch decisions</a>', index)
        self.assertNotIn('href="q3.html"', index)
        self.assertEqual(index.count('href="q3.html#Q3.1"'), 1)
        self.assertIn("<td>2 of 2 open</td>", index)
        for prompt in ("Two open questions, saved to decide later.", "Merge the tide tables now?", "Which colour for the quay?"):
            self.assertNotIn(prompt, index.split("<h2>References</h2>")[0])
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
                source = ["--via", "chat"] if state == "answered" else []
                self.assertEqual(self.run_cli("mark", "Q4", state, *source)[0], 0)
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
        self.assertIn('<td>None open</td><td>Answered <span>Not delivered</span></td>', page.split("<dialog", 1)[0])
        self.assertIn('<a class="card-link" href="q1.html"><span class="id">Q1</span> Pier lights</a>', page)
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
        self.assertEqual(re.findall(r'<a class="lib-title" href="([^"]+)">([^<]+)</a>', answered),
                         [("q4.html", "Pier lights")])
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
                              '<span class="sum-a"><span class="sum-text">Answer 1</span>'
                              '<span class="sum-source" hidden> (Desk)</span></span></summary>', body)
                self.assertIn("1 of 2 open", page)
                self.assertEqual(page.count('class="sendbar"'), 1 if served else 0)
                if served:
                    self.assertGreater(body.index('class="sendbar"'), body.index("</details>"))
                self.assertNotIn("Return to Desk</a>", page)
        self.assertEqual(self.run_cli("mark", "Q1.1", "open")[0], 0)
        self.assertEqual(self.run_cli("mark", "Q1.1", "answered", "--via", "chat")[0], 0)
        repeat = quarterdeck.send_answer(quarterdeck.store_path(), None,
                                         {"part": "Q1.1", "answer": "Answer 1", "revision": ""})
        self.assertEqual((repeat["answer"], repeat["delivery"]), ("Answer 1", "sent"))
        self.assertEqual(repeat["off_desk_answer"]["via"], "chat")
        item = quarterdeck.read_store(quarterdeck.store_path())["items"][0]
        for served in (True, False):
            with self.subTest(served=served, via="chat"):
                page = quarterdeck.item_page(item, "Quarterdeck", served=served)
                summary, expanded = page.split('<details class="done">', 1)[1].split("</summary>", 1)
                self.assertIn('<span class="sum-text">Answer 1</span><span class="sum-source"> (Desk)</span>', summary)
                self.assertIn("Answered elsewhere · chat", expanded)
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
        self.assertNotIn(expected, pages["index.html"].split("<h2>References</h2>")[0])
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


def anchor_destinations(page: str) -> set[str]:
    destinations = set()

    class Anchors(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                destinations.update(value for name, value in attrs if name == "href" and value is not None)

    Anchors().feed(page)
    return destinations


def has_element_id(page: str, tag_name: str, element_id: str) -> bool:
    class Elements(HTMLParser):
        found = False

        def handle_starttag(self, tag, attrs):
            if tag == tag_name and ("id", element_id) in attrs:
                self.found = True

    parser = Elements()
    parser.feed(page)
    parser.close()
    return parser.found


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
        addresses = {prompt_url, context_url, option_url, suggestion_url}
        for desk in (pages["index.html"], quarterdeck.list_page(data, "Quarterdeck", served=True)):
            self.assertTrue(addresses.isdisjoint(anchor_destinations(desk)))
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
        self.assertTrue({link["url"] for link in expected_links}.isdisjoint(anchor_destinations(pages["index.html"])))

    def test_desk_rows_do_not_repeat_question_links(self) -> None:
        later_question = {"question": "Where should the button go?", "context": "Review the placements.",
                          "options": ["Top", "Bottom"],
                          "links": [{"text": "See the placements", "url": "https://example.org/review/placements"}]}
        self.assertEqual(self.save([{**LINKED[1], "links": []}, LINKED[0], later_question])[0], 0)
        self.assertNotIn("links", self.store()["items"][0]["body"]["questions"][0])
        destinations = {link["url"] for question in (LINKED[0], later_question) for link in question["links"]}
        cases = [("Q1.2", "open"), ("Q1.2", "answered"), ("Q1.2", "closed"),
                 ("Q1.3", "closed"), ("Q1.3", "open"), ("Q1.2", "open")]
        for part, state in cases:
            with self.subTest(part=part, state=state):
                self.assertEqual(self.run_cli("mark", part, state, *(["--via", "chat"] if state == "answered" else []))[0], 0)
                data = quarterdeck.read_store(quarterdeck.store_path())
                pages = quarterdeck.site_pages(data, "Quarterdeck", "index.html")
                served_desk = quarterdeck.list_page(data, "Quarterdeck", served=True)
                served_item = quarterdeck.item_page(data["items"][0], "Quarterdeck", served=True)
                for desk, item in ((pages["index.html"], pages["q1.html"]), (served_desk, served_item)):
                    self.assertEqual(destinations & anchor_destinations(desk), set())
                    self.assertEqual(destinations & anchor_destinations(item), destinations)

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
        self.assertIn('<a class="card-link" href="q3.html#Q3.1"><span class="id">Q3</span> Tide launch decisions</a>',
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
    def start(self, base_path: str = "", *, home: Path | None = None,
              ask_home: Path | None = None, workers_home: Path | None = None) -> int:
        handler = quarterdeck.make_handler("Quarterdeck", base_path, home=home, ask_home=ask_home, workers_home=workers_home)
        server = quarterdeck.make_server("127.0.0.1", 0, handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def fetch(self, port: int, path: str, method: str = "GET", host: str | None = None,
              user_agent: str | None = None) -> tuple[int, str, dict]:
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
        if host:
            request.add_header("Host", host)
        if user_agent:
            request.add_header("User-Agent", user_agent)

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
        desk = self.fetch(port, "/qd/")[1].split("<h1>Desk</h1>", 1)[1]
        self.assertNotIn("Which colour for the quay?", desk)
        self.assertNotIn("Merge the tide tables now?", desk)
        self.assertIn('<td>1 of 2 open</td>', desk)
        self.assertIn('<a class="card-link" href="q3.html#Q3.2"><span class="id">Q3</span> Tide launch decisions</a>', desk)
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
        desk = self.fetch(port, "/qd/")[1].split("<h1>Desk</h1>", 1)[1]
        self.assertIn('<tr class="work"><th scope="row"><a class="card-link" href="q2.html"', desk)
        self.assertIn('<tr class="work next"><th scope="row"><a class="card-link" href="q1.html#Q1.1"', desk)
        self.assertNotIn("Merge the tide tables now?", desk)
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

    def test_answer_retries_after_a_store_read_fails(self):
        self.add_all_kinds()
        port = self.start("/qd", home=self.root / "home")
        path = quarterdeck.store_path()
        read_text = type(path).read_text
        def read_with_failure(file, *args, **kwargs):
            if file == path:
                raise PermissionError("store read failed")
            return read_text(file, *args, **kwargs)

        before = self.store()
        with patch.object(type(path), "read_text", read_with_failure):
            self.assertEqual(self.post_answer(port, "Q3.1", "Hold"), (500, b"Could not save the answer."))
        self.assertEqual(self.store(), before)
        status, record = self.post_answer(port, "Q3.1", "Hold")
        self.assertEqual((status, record["answer"]), (200, "Hold"))

    def test_answer_uses_one_store_read_with_or_without_delivery(self):
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
        for port, part, answer, revision, delivery in (
            (unbound, "Q3.1", "Hold", "", "pending"),
            (bound, "Q3.2", "Blue", sent["revision"], "sent"),
            (bound, "Q3.2", "Red", sent["revision"], "sent"),
        ):
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
                    status, record = self.post_answer(port, part, answer, revision)
                self.assertEqual(status, 200)
                self.assertEqual((record["answer"], record["delivery"]), (answer, delivery))

    def test_removed_item_conflicts_before_save(self):
        self.add_all_kinds()
        home = self.root / "home"
        port = self.start("/qd", home=home)
        self.assertEqual(self.fetch(port, "/qd/q3.html")[0], 200)
        self.assertEqual(self.run_cli("remove", "Q3")[0], 0)
        self.assertEqual(self.post_answer(port, "Q3.1", "Hold"), (409, b"Item removed. Reload before sending."))
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

    def check_answer_page(self, port, script, path="/qd/q1.html", *, viewport=None, device_scale_factor=None):
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
            page = (f'<iframe style="width:{width}px;height:{height}px;border:0" srcdoc="{escape(page)}"></iframe>'
                    '<output id="browser-result">Incomplete</output>')
            # Give resize observers a rendered frame before headless Chrome dumps the fixture.
            timing = ["--run-all-compositor-stages-before-draw", "--virtual-time-budget=1000"]
        fixture.write_text(page, encoding="utf-8")
        scale = [] if device_scale_factor is None else [f"--force-device-scale-factor={device_scale_factor}"]
        result = subprocess.run([chrome, "--headless", "--disable-background-networking", "--no-first-run", "--lang=en-US",
                                 f"--user-data-dir={self.root / 'browser'}", *scale, *timing, "--dump-dom", fixture.as_uri()],
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
        self.assertEqual(self.run_cli("mark", "Q1.3", "open")[0], 0)
        self.assertEqual(self.run_cli("mark", "Q1.3", "answered", "--via", "chat")[0], 0)
        for width in (320, 390, 1440):
            with self.subTest(width=width):
                self.check_answer_page(port, r"""
const list = document.querySelector('#questions');
const order = () => [...list.children].map(row => row.id || row.lastElementChild.id).join();
const checkDeskMarker = summary => {
  const source = summary.querySelector('.sum-source');
  check(source && source.textContent === ' (Desk)', 'The folded answer lacked a trailing Desk marker');
  const range = document.createRange();
  range.selectNodeContents(source);
  const marker = range.getBoundingClientRect();
  const answer = source.closest('.sum-a').getBoundingClientRect();
  check(marker.width > 0 && marker.left >= answer.left && marker.right <= answer.right + 1,
        'The Desk marker was clipped at ' + innerWidth + ' pixels');
};
const checkVisibleWord = (summary, word) => {
  const answer = summary.querySelector('.sum-text');
  check(answer.textContent === word, 'The folded answer had the wrong text');
  const range = document.createRange();
  range.setStart(answer.firstChild, 0);
  range.setEnd(answer.firstChild, word.length);
  const wordBounds = range.getBoundingClientRect();
  const style = getComputedStyle(answer);
  const ellipsisSpace = answer.scrollWidth > answer.clientWidth ? parseFloat(style.fontSize) : 0;
  check(wordBounds.width > 0 && wordBounds.right + ellipsisSpace <= answer.getBoundingClientRect().right + 1,
        'The one-word answer was clipped at ' + innerWidth + ' pixels');
};
const checkSummary = summary => {
  const question = summary.querySelector('.sum-q');
  const answer = summary.querySelector('.sum-text');
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
checkDeskMarker(initiallyFolded.querySelector('summary'));
let delivery = 'sent';
let offDeskAnswer = null;
let focusDuringSend;
window.fetch = async (url, options) => {
  const {answer} = JSON.parse(options.body);
  if (focusDuringSend) {
    focusDuringSend.focus();
    check(document.activeElement === focusDuringSend, 'The focus fixture did not reach the row during delivery');
  }
  return {ok: true, json: async () => ({answer, state: 'answered', revision: 'saved-revision',
    answered_at: '2026-01-12T15:00:00Z', delivery, off_desk_answer:offDeskAnswer})};
};
typeAnswer(2, 'Blue');
focusDuringSend = card(2).querySelector('a');
await sendBatch();
const secondSummary = card(2).closest('details').querySelector('summary');
checkVisibleWord(secondSummary, 'Blue');
check(secondSummary.querySelector('.sum-source').getClientRects().length === 0,
      'A Desk-only answer displayed a provenance marker');
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
typeAnswer(1, 'Hold');
offDeskAnswer = {via: 'chat', answered_at: '2026-01-11T12:00:00Z'};
focusDuringSend = card(1).querySelector('a');
await sendBatch();
check(!folded.open, 'The delivered correction did not close the row');
checkVisibleWord(summary, 'Hold');
checkDeskMarker(summary);
check(document.activeElement === summary, 'Refolding a correction dropped focus from the row');
summary.click();
const source = card(1).querySelector('.answer-source');
const sourceTime = source.querySelector('time');
check(source.innerText.includes('Answered elsewhere · chat') &&
      sourceTime?.dateTime === '2026-01-11T12:00:00Z' && sourceTime.innerText.includes('Jan 11, 2026'),
      'The expanded correction lost the latest answer source or its timestamp');
typeAnswer(1, 'Use the next tide');
delivery = 'pending';
offDeskAnswer = {via: 'email', answered_at: '2026-01-12T05:00:00Z'};
focusDuringSend = summary;
await sendBatch();
check(order() === 'Q1.1,Q1.2,Q1.3' && card(1).parentElement === list,
      'The pending correction did not return to its place among expanded questions');
check(card(1).querySelector('.delivery').textContent === 'Not delivered', 'The pending correction lost its delivery status');
check(source.innerText.includes('Answered elsewhere · email') &&
      source.querySelector('time')?.dateTime === '2026-01-12T05:00:00Z',
      'The pending correction retained the previous answer source or timestamp');
check(document.activeElement === card(1).querySelector('a'), 'Expanding a pending correction lost focus from its removed summary');
secondSummary.click();
typeAnswer(2, 'Red');
focusDuringSend = card(2).querySelector('a');
await sendBatch();
check(document.activeElement === focusDuringSend, 'Expanding a pending correction dropped focus from its existing link');
""", viewport=(width, 844))

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

    def test_library_layout_covers_fractional_breakpoint_widths(self):
        port = self.start("/qd")
        for drawer in (False, True):
            with self.subTest(drawer=drawer):
                self.check_answer_page(port, f"const drawer = {json.dumps(drawer)};\n" + r"""
const widthQuery = drawer ? '(640px < width < 640.16px)' : '(width: 640px)';
check(matchMedia(widthQuery).matches, 'The fixture did not reach ' + widthQuery);
const width = visualViewport.width;
button('open-library').click();
const dialog = button('library');
for (const animation of dialog.getAnimations()) animation.finish();
const bounds = dialog.getBoundingClientRect();
const near = (actual, expected) => Math.abs(actual - expected) < 1;
check(near(bounds.right, width) && near(bounds.bottom, innerHeight), 'The library lost its viewport anchoring');
check(drawer ? near(bounds.top, 0) && bounds.left > 0 : bounds.top > 0 && near(bounds.left, 0),
      'The library used the wrong layout at ' + width + 'px');
for (const tab of dialog.querySelectorAll('[role="tab"]')) {
  tab.click();
  const search = button(tab.getAttribute('aria-controls')).querySelector('input[type="search"]');
  const target = search.getBoundingClientRect();
  check(search === document.elementFromPoint(target.left + target.width / 2, target.top + target.height / 2),
        'The search field was hidden at ' + width + 'px');
}
""", path="/qd/", viewport=(640.125 if drawer else 640, 800),
                                       device_scale_factor=1.24975 if drawer else None)

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

    def test_stacked_library_filters_leave_results_reachable(self):
        self.add("resource", "Harbor chart", "Review the route.", "--url", "https://example.org/chart",
                 "--link-text", "Open chart")
        long_project = "Waterfront Maintenance and Lighting Plan"
        for project in (long_project, "Harbor"):
            item = self.add("questions", project, "Choose an option.", "--project", project, "--tag", "research",
                            "--body-file", "-", stdin=json.dumps(QUESTIONS[:1]))
            self.assertEqual(self.run_cli("mark", item, "closed")[0], 0)
        self.check_answer_page(self.start("/qd"), r"""
check(innerWidth === 320 && innerHeight === 355, 'Wrong fixture viewport');
button('open-library').click();
const dialog = button('library');
for (const animation of dialog.getAnimations()) animation.finish();
const reach = control => {
  control.scrollIntoView({block:'center'});
  const bounds = control.getBoundingClientRect();
  check(control.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)),
        'The library hid ' + (control.textContent || control.id));
};
const answered = button('tab-answered');
reach(answered);
answered.click();
const panel = button('panel-answered');
const project = panel.querySelector('select[name="project"]');
const tag = panel.querySelector('select[name="tag"]');
check(project.getBoundingClientRect().bottom <= tag.getBoundingClientRect().top, 'The filter fixture did not stack');
reach(project);
reach(tag);
project.selectedIndex = 1;
project.dispatchEvent(new Event('change', {bubbles:true}));
const links = [...panel.querySelectorAll('.lib-item:not([hidden]) .lib-title')];
check(links.length === 1 && links[0].getAttribute('href') === 'q3.html', 'The project filter did not select the Harbor item');
reach(links[0]);
check(dialog.scrollTop > 0, 'The results were not reachable by scrolling the whole library');
const references = button('tab-references');
reach(references);
references.click();
reach(button('panel-references').querySelector('a[href="q1.html"]'));
""", path="/qd/", viewport=(320, 355))

    def test_desk_row_stretches_its_one_link_across_the_row(self):
        question = {**QUESTIONS[1], "question": "Use https://example.org/options?",
                    "links": [{"text": "Review the chart", "url": "https://example.org/review"}]}
        self.add("questions", "Pier lights", "See https://example.org/notes", "--body-file", "-",
                 stdin=json.dumps([QUESTIONS[0], question]))
        self.add("questions", "Tide tables", "Check the latest plan.", "--project", "W" * 40, "--body-file", "-",
                 stdin=json.dumps(QUESTIONS))
        self.add("questions", "\u200b", "Review the chart.", "--body-file", "-",
                 stdin=json.dumps(QUESTIONS[:1]))
        self.assertEqual(self.run_cli("mark", "Q1.1", "closed")[0], 0)
        port = self.start("/qd")
        for part, answer in (("Q2.2", "Blue"), ("Q3.1", "Merge")):
            status, record = self.post_answer(port, part, answer)
            self.assertEqual((status, record["delivery"]), (200, "pending"))
        script = r"""
const desk = document.querySelector('.desk');
const rows = desk.querySelectorAll('table tr:not(thead tr)');
check(rows.length === 3, 'The Desk should have one row per fixture item');
const [pending, first, second] = rows;
const project = first.querySelector('.project');
check(project.checkVisibility() && project.textContent === 'W'.repeat(40), 'The Project column lost the saved name');
check(desk.scrollWidth === desk.clientWidth, 'A long project widened the Desk beyond its container');
check(first.cells[3].textContent === 'Asked Not delivered', 'An open row lost its delivery notice');
check(pending.cells[3].textContent === 'Answered Not delivered', 'A pending-only row lost its delivery notice');
const hit = node => {
  node.scrollIntoView({block:'center'});
  const rect = node.getClientRects()[0];
  return document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)?.closest('a');
};
for (const [row, destination, label] of [
  [pending, 'q3.html', 'Q3'], [first, 'q2.html#Q2.1', 'Q2 Tide tables'], [second, 'q1.html#Q1.2', 'Q1 Pier lights']
]) {
  const links = [...row.querySelectorAll('a')];
  const action = links[0];
  check(action && links.length === 1 && action.getAttribute('href') === destination, 'A row had several links or the wrong destination');
  check(action.innerText.replace('\u200b', '').replace(/\s+/g, ' ').trim() === label,
        `The item link did not have the readable label "${label}"`);
  check(hit(action) === action, 'The title link missed its hit target');
  for (const cell of row.cells) {
    check(hit(cell) === action, `Cell ${cell.cellIndex + 1} did not open ${destination}`);
  }
  action.focus();
  check(document.activeElement === action && getComputedStyle(row).outlineStyle === 'solid', 'The row lost its keyboard focus ring');
}
"""
        for viewport in ((390, 844), (641, 900), (1440, 900)):
            with self.subTest(viewport=viewport):
                self.check_answer_page(port, script, path="/qd/", viewport=viewport)

    def test_restored_desk_reloads_archive_membership_and_search(self):
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the Desk history browser test")
        for title, project, tag in (("Pier lights", "Beta", "research"), ("Tide tables", "Alpha", "product-design"),
                                    ("Harbor chart", "Beta", "research"), ("Berth markers", "Beta", "research")):
            self.add("questions", title, "Choose an option.", "--project", project, "--tag", tag,
                     "--body-file", "-", stdin=json.dumps(QUESTIONS[:1]))
        self.assertEqual(self.run_cli("mark", "Q4", "answered", "--via", "chat")[0], 0)
        home = self.root / "home"
        inbox = home / "bin" / "fm-inbox.sh"
        inbox.parent.mkdir(parents=True)
        inbox.write_text("#!/bin/sh\nexit 0\n")
        inbox.chmod(0o700)
        finished = threading.Event()
        results = []
        handler = quarterdeck.make_handler("Quarterdeck", "/qd", home=home)
        run_cli = self.run_cli

        class HistoryTestHandler(handler):
            def do_POST(self):
                if self.path == "/qd/history-test-result":
                    results.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                    self.send_text(200, "")
                    finished.set()
                elif self.path == "/qd/history-test-reclassify":
                    code, _, error = run_cli("classify", "Q2", "--project", "Beta", "--tag", "research")
                    self.send_text(200 if code == 0 else 500, error)
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
const project = panel.querySelector('select[name="project"]');
const tag = panel.querySelector('select[name="tag"]');
const choose = (filter, value) => {
  filter.value = value;
  filter.dispatchEvent(new Event('change', {bubbles:true}));
};
const shown = () => [...panel.querySelectorAll('.lib-item:not([hidden]) .lib-title')].map(link => link.getAttribute('href')).join();
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
    choose(project, 'Alpha');
    choose(tag, 'product-design');
    check(find('saffron') === 'q2.html', 'The original answer did not match its search');
    panel.querySelector('a[href="q2.html"]').click();
  } else if (visits <= 2) {
    check(!desk.textContent.includes('Pier lights'), 'The restored Desk still showed the completed item');
    check(dialog.open && tab.getAttribute('aria-selected') === 'true' && !panel.hidden,
          'The library did not reopen on Answered after item visit ' + visits);
    check(project?.value === 'Alpha' && project.checkVisibility(), 'The missing project filter was not restored');
    check(tag?.value === 'product-design' && tag.checkVisibility() && tag.selectedOptions[0].text === 'product design',
          'The missing tag choice was not restored');
    check(search.value === 'saffron' && shown() === '', 'The saved search was lost or still used the old answer');
    check(panel.querySelector('a[href="q1.html"]'), 'The completed item was missing from Answered');
    check(find('') === '' && panel.querySelector('[role="status"]').textContent.includes('No results'),
          'The unavailable filters admitted unrelated results');
    choose(project, '');
    check(shown() === '', 'Clearing the project also cleared the tag');
    choose(tag, '');
    check(find('saffron') === '' && find('ultramarine') === 'q2.html',
          'Clearing the filters did not restore search against fresh answers');
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
      const response = await fetch('history-test-reclassify', {method:'POST'});
      if (!response.ok) throw new Error('The fixture item was not reclassified');
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
        self.add("resource", "Harbour map Straße ΧΑΡΤΗΣ [A+B]", description,
                 "--url", "https://example.org/map", "--link-text", "Open the map")
        self.add("list", "Harbour supplies", "Tools for the pilot.", "--body-file", "-", stdin="Rope")
        home = self.root / "home"
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        port = self.start("/qd", home=home)
        with patch.object(quarterdeck, "now_text", return_value="2026-01-10T16:25:00Z"):
            self.assertEqual(self.post_answer(port, "Q2.1", "Use amber ΔΡΟΜΟΣ at the office")[1]["delivery"], "sent")
        self.assertEqual(self.run_cli("mark", "Q2.2", "answered", "--via", "chat")[0], 0)
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
check(dialog.querySelectorAll('input[type="search"]').length === 3 && !dialog.querySelector('input[type="date"]'),
      'The library does not have exactly one search per tab');
for (const query of ['pier lamp merge amber', 'Σ amber', 'oﬃce amber', 'Q2', 'q2.1', 'q2.2']) {
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
references.value = 'STRASSE Σ [a+b] chart';
references.dispatchEvent(new Event('input', {bubbles: true}));
check([...document.querySelectorAll('#panel-references .lib-item')].filter(row => !row.hidden).length === 1, 'References search failed');
check(!references.closest('[role="tabpanel"]').querySelector('[role="status"]').textContent, 'References search reported a match count');
references.value = '.*';
references.dispatchEvent(new Event('input', {bubbles: true}));
check(![...document.querySelectorAll('#panel-references .lib-item')].some(row => !row.hidden),
      'A regular expression metacharacter matched as a pattern');
references.value = 'chart';
references.dispatchEvent(new Event('input', {bubbles: true}));
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
        log = self.root / "state" / "quarterdeck" / "visits.jsonl"
        self.assertEqual([json.loads(line)["page"] for line in log.read_text(encoding="utf-8").splitlines()], ["main"])

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
            self.assertFalse(has_element_id(body, "button", "refresh-workers"))
            self.assertEqual(self.fetch(port, "/qd/workers.json")[0], 404)
            self.assertEqual(self.fetch(port, "/qd/workers-saved.json")[0], 404)
            before = quarterdeck.store_path().read_bytes()
            self.assertEqual(self.fetch(port, "/", method="POST")[0], 405)
            self.assertEqual(quarterdeck.store_path().read_bytes(), before)
            status, record = self.post_answer(port, "Q3.1", "Hold")
            self.assertEqual((status, record["delivery"]), (200, "sent"))
            self.assertEqual((home / "received-note").read_text(), "Q3.1 Hold")

    def test_every_served_desk_load_is_logged_under_old_and_current_service_flags(self) -> None:
        self.add_all_kinds()
        path = self.root / "state" / "quarterdeck" / "visits.jsonl"
        phone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
        tablet = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Mobile/15E148"
        # An older unit passes --allow-marks and --watch-waits, which serve ignores; a current unit passes neither.
        for flags in (("--allow-marks", "--watch-waits"), ()):
            with self.subTest(flags=flags):
                path.unlink(missing_ok=True)
                with self.serve_cli("--base-path", "/qd", *flags) as port:
                    self.assertEqual(self.fetch(port, "/qd/", user_agent=phone)[0], 200)
                    self.assertEqual(self.fetch(port, "/qd/index.html", user_agent=tablet)[0], 200)
                    self.assertEqual(self.fetch(port, "/qd/q3.html")[0], 200)
                    self.assertEqual(self.fetch(port, "/qd/missing.html")[0], 404)
                    self.assertEqual(self.fetch(port, "/qd/", host="attacker.example")[0], 403)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([(r["page"], r["device"]) for r in records], [("main", "phone"), ("main", "desktop")])
                for record in records:
                    self.assertEqual(set(record), {"ts", "page", "device"})
                    self.assertRegex(record["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_visit_log_preserves_and_separates_an_unterminated_tail(self) -> None:
        path = self.root / "state" / "quarterdeck" / "visits.jsonl"
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        old = b'{"ts":"2026-01-01T00:00:00.000Z","page":"main","device":"desktop"}\n'
        history = old * 2000
        path.parent.mkdir(parents=True)
        for tail in (old.rstrip(b"\n"), b'{"ts": "2026-06'):
            with self.subTest(tail=tail):
                path.write_bytes(history + tail)
                path.chmod(0o644)
                quarterdeck.VisitLog(path, clock=lambda: now).record(None)
                content = path.read_bytes()
                prefix = history + tail + b"\n"
                self.assertEqual(content[:len(prefix)], prefix)
                appended = content[len(prefix):].splitlines(keepends=True)
                self.assertEqual(len(appended), 1)
                self.assertTrue(appended[0].endswith(b"\n"))
                self.assertEqual(json.loads(appended[0]),
                                 {"ts": "2026-06-01T00:00:00.000Z", "page": "main", "device": "desktop"})
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_visit_log_rolls_back_and_reports_a_short_write(self) -> None:
        path = self.root / "state" / "quarterdeck" / "visits.jsonl"
        log = quarterdeck.VisitLog(path)
        log.record(None)
        original = path.read_bytes()
        write = os.write

        for before in (original, original.rstrip(b"\n")):
            with self.subTest(terminated=before.endswith(b"\n")):
                path.write_bytes(before)
                with patch.object(os, "write", side_effect=lambda descriptor, data: write(descriptor, data[:-1])):
                    with self.assertRaisesRegex(OSError, "incomplete visit log write"):
                        log.record(None)
                self.assertEqual(path.read_bytes(), before)

    def test_visit_log_never_follows_a_symbolic_link(self) -> None:
        path = self.root / "state" / "quarterdeck" / "visits.jsonl"
        path.parent.mkdir(parents=True)
        target = self.root / "elsewhere"
        path.symlink_to(target)
        with self.assertRaises(OSError) as error:
            quarterdeck.VisitLog(path).record(None)
        self.assertEqual(error.exception.errno, errno.ELOOP)
        self.assertFalse(target.exists())

    def test_a_visit_that_cannot_be_logged_still_serves_the_desk(self) -> None:
        self.add_all_kinds()
        (self.root / "state" / "quarterdeck" / "visits.jsonl").mkdir(parents=True)
        with self.serve_cli() as port:
            status, body, _headers = self.fetch(port, "/")
        self.assertEqual(status, 200)
        self.assertIn("Tide launch decisions", body)

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
        workers_snapshot = quarterdeck.workers_snapshot

        def waiting_snapshot(home):
            snapshot = workers_snapshot(home)
            entered.set()
            release.wait(timeout=10)
            return snapshot

        with patch.object(quarterdeck, "workers_snapshot", waiting_snapshot), ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.workers, port)
            try:
                self.assertTrue(entered.wait(timeout=5))
                error = {"error": "The workers could not be read."}
                self.assertEqual(self.workers(port), (502, error))
                self.assertIsNone(self.workers(port, "/qd/workers-saved.json")[1]["snapshot"])
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
        self.assertEqual(self.fetch(port, "/qd/workers-saved.json")[0], 404)
        page = self.fetch(port, "/qd/workers.html")[1]
        self.assertIn("Workers show here once firstmate_home names a Firstmate home.", page)
        self.assertFalse(has_element_id(page, "button", "refresh-workers"))

    def test_the_desk_links_to_the_workers_page_and_never_reads_the_fleet(self) -> None:
        port = self.start("/qd", workers_home=self.make_home([snapshot_task("Wren")]))
        status, page, _ = self.fetch(port, "/qd/")
        self.assertEqual(status, 200)
        self.assertIn('<a href="workers.html">Workers</a>', page)
        self.assertFalse(has_element_id(page, "section", "workers"))
        self.assertFalse(has_element_id(page, "button", "refresh-workers"))
        status, workers_page, _ = self.fetch(port, "/qd/workers.html")
        self.assertEqual(status, 200)
        self.assertIn('id="refresh-workers">Check workers</button>', workers_page)
        self.assertEqual(workers_page.count("<h1>"), 1)

        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the Desk browser test")
        steps = """
  await quiet();
  const workerPaths = new Set(['/qd/workers-saved.json', '/qd/workers.json']);
  check(!performance.getEntriesByType('resource').some(entry => workerPaths.has(new URL(entry.name).pathname)),
        'The Desk made a Workers request');
"""
        with patch.object(quarterdeck, "list_page", self.instrumented_page(quarterdeck.list_page, steps)), \
                patch.object(quarterdeck, "run_snapshot", wraps=quarterdeck.run_snapshot) as run_snapshot:
            outcome, stderr = self.browser_page(port, "/qd/")
            run_snapshot.assert_not_called()
        self.assertEqual(outcome, "PASS", stderr)

    def test_the_workers_page_obeys_host_checks(self) -> None:
        port = self.start("/qd", workers_home=self.make_home([]))
        for path in ("/qd/workers.json", "/qd/workers-saved.json", "/qd/workers.html"):
            self.assertEqual(self.fetch(port, path, host="intruder.example")[0], 403, path)

    def test_a_reading_is_saved_in_memory_and_goes_stale_after_an_hour(self) -> None:
        port = self.start("/qd", workers_home=self.make_home())
        saved_path = "/qd/workers-saved.json"
        self.assertEqual(self.workers(port, saved_path), (200, {"snapshot": None, "stale": True}))
        clock = MagicMock()
        with patch.object(quarterdeck, "time", SimpleNamespace(monotonic=clock)):
            for checked_at, source_time in ((100, "2000-01-01T00:00:00Z"), (4000, "2099-01-01T00:00:00Z")):
                with self.subTest(source_time=source_time):
                    self.make_home(body=json.dumps({"generated": source_time, "tasks": []}))
                    clock.return_value = checked_at
                    status, snapshot = self.workers(port)
                    self.assertEqual(status, 200)
                    self.assertEqual(snapshot["observed_at"], source_time)
                    for elapsed, stale in ((0, False), (3600, False), (3601, True)):
                        clock.return_value = checked_at + elapsed
                        self.assertEqual(self.workers(port, saved_path), (200, {"snapshot": snapshot, "stale": stale}))

    def test_suspend_time_expires_the_reading_for_lookup_and_automatic_refresh(self) -> None:
        port = self.start("/qd", workers_home=self.make_home([snapshot_task("Wren")]))
        clock = SimpleNamespace(CLOCK_BOOTTIME=7, CLOCK_MONOTONIC=1)
        times = {clock.CLOCK_BOOTTIME: 100, clock.CLOCK_MONOTONIC: 100}
        clock.clock_gettime = times.__getitem__
        clock.monotonic = lambda: times[clock.CLOCK_MONOTONIC]
        saved_path = "/qd/workers-saved.json"
        with patch.object(quarterdeck, "time", clock):
            status, initial = self.workers(port)
            self.assertEqual(status, 200)
            self.assertEqual(self.workers(port, saved_path), (200, {"snapshot": initial, "stale": False}))
            times[clock.CLOCK_BOOTTIME] += 3601
            self.assertEqual(self.workers(port, saved_path), (200, {"snapshot": initial, "stale": True}))
            self.make_home([snapshot_task("Bracken")])
            status, refreshed = self.workers(port, "/qd/workers.json?reuse=1")
            self.assertEqual(status, 200)
            self.assertEqual([row["name"] for row in refreshed["workers"]], ["Bracken"])
            self.assertEqual(self.workers(port, saved_path), (200, {"snapshot": refreshed, "stale": False}))

    def test_automatic_refresh_rechecks_freshness_after_another_visitor_refreshes(self) -> None:
        port = self.start("/qd", workers_home=self.make_home([snapshot_task("Wren")]))
        automatic_path = "/qd/workers.json?reuse=1"
        saved_path = "/qd/workers-saved.json"
        with patch.object(quarterdeck, "workers_clock", return_value=100) as clock:
            self.assertEqual(self.workers(port)[0], 200)
            clock.return_value += 3601
            for _ in range(2):
                self.assertTrue(self.workers(port, saved_path)[1]["stale"])

            self.make_home([snapshot_task("Bracken")])
            status, refreshed = self.workers(port, automatic_path)
            self.assertEqual(status, 200)
            self.assertEqual([row["name"] for row in refreshed["workers"]], ["Bracken"])

            self.make_home([])
            self.assertEqual(self.workers(port, automatic_path), (200, refreshed))
            status, direct = self.workers(port)
            self.assertEqual(status, 200)
            self.assertEqual(direct["workers"], [])
            self.make_home(code=1)
            self.assertEqual(self.workers(port, "/qd/workers.json?reuse=1&extra=1")[0], 502)
            self.assertEqual(self.workers(port, saved_path)[1]["snapshot"], direct)

    def test_a_failed_reading_keeps_the_saved_one(self) -> None:
        home = self.make_home([snapshot_task("Wren")])
        port = self.start("/qd", workers_home=home)
        snapshot = self.workers(port)[1]
        (home / "snapshot.json").write_text("not json")
        self.assertEqual(self.workers(port)[0], 502)
        self.assertEqual(self.workers(port, "/qd/workers-saved.json")[1]["snapshot"], snapshot)

    def browser_page(self, port, route="/qd/workers.html", *, budget=5000):
        chrome = shutil.which("google-chrome")
        result = subprocess.run([chrome, "--headless", "--disable-background-networking", "--no-first-run",
                                 f"--user-data-dir={self.root / 'browser'}", "--dump-dom", f"--virtual-time-budget={budget}",
                                 f"http://127.0.0.1:{port}{route}"],
                                capture_output=True, text=True, timeout=30, check=True)
        return result.stdout.partition('<output id="browser-result">')[2].partition("</output>")[0], result.stderr

    BROWSER_HELPERS = """
<output id="browser-result">Incomplete</output>
<script>
const result = document.getElementById('browser-result');
const statusLine = document.getElementById('workers-status');
const roster = document.getElementById('workers-list');
const button = document.getElementById('refresh-workers');
const settled = () => new Promise(resolve => {
  const watcher = new MutationObserver(() => {
    if (!button.hasAttribute('aria-disabled')) { watcher.disconnect(); resolve(); }
  });
  watcher.observe(button, {attributes: true});
});
const requestCount = name => performance.getEntriesByName(new URL(name, location.href).href).length;
const check = (condition, message) => { if (!condition) throw new Error(message); };
const quiet = () => new Promise(resolve => setTimeout(resolve, 1500));
"""

    def instrumented_page(self, page, steps):
        def instrumented(*args, **kwargs):
            return page(*args, **kwargs).replace(
                "</body>", self.BROWSER_HELPERS + "(async () => {\n" + steps +
                "\n  result.textContent = 'PASS';\n})().catch(error => { result.textContent = error.message; });\n</script>\n</body>")
        return instrumented

    def test_a_missing_reading_is_fetched_on_entry_and_then_only_on_the_button(self) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the workers browser test")
        skerry = snapshot_task("Skerry", kind="secondmate", alive="alive")
        tern = snapshot_task("Tern", kind="secondmate", alive="alive")
        home = self.make_home([snapshot_task("Wren", decisions=["map-scale"]), skerry, tern])
        port = self.start("/qd", workers_home=home)
        next_snapshot = {
            "generated": "2026-03-02T13:39:00Z",
            "tasks": [snapshot_task("Wren", exists=False), snapshot_task("Bracken", state="paused"), skerry],
        }
        run_snapshot = quarterdeck.run_snapshot

        def advance_fixture(home):
            snapshot = run_snapshot(home)
            if snapshot["generated"] == SNAPSHOT_TIME:
                (home / "snapshot.json").write_text(json.dumps(next_snapshot))
            return snapshot

        steps = """
  const headings = () => [...document.querySelectorAll('#workers :is(h1,h2,h3,h4,h5,h6)')]
    .map(heading => heading.tagName + ' ' + heading.textContent).join('|');
  check(statusLine.textContent === 'Loading workers', 'Entry without a reading must show that it is loading');
  button.click();
  await settled();
  check(requestCount('workers-saved.json') === 1, 'Expected one saved read on entry');
  check(requestCount('workers.json?reuse=1') === 1, 'Expected one worker fetch on entry with nothing saved');
  check(requestCount('workers.json') === 0, 'A check overlapped entry');
  check(roster.textContent.includes('Wren') && !roster.textContent.includes('Bracken'), 'The initial roster did not appear');
  check(headings() === 'H1 Workers|H2 Second mates (2)', 'Second mates must follow Workers at heading level 2');
  check(roster.querySelector('.status.exception')?.textContent === 'Question asked', 'The exception badge was missing');
  check(statusLine.querySelector('time')?.dateTime === '2026-03-02T13:40:00Z', 'The initial observation time was wrong');
  await new Promise(resolve => setTimeout(resolve, 61000));
  check(requestCount('workers.json?reuse=1') === 1 && requestCount('workers.json') === 0,
        'A worker request ran while the page was idle');
  const refreshed = settled();
  button.click();
  await refreshed;
  check(requestCount('workers.json') === 1, 'The check button did not make exactly one worker request');
  check(!roster.textContent.includes('Wren') && roster.textContent.includes('Bracken'), 'The refreshed roster did not replace the old one');
  check(headings() === 'H1 Workers|H2 Second mates', 'A single second mate must keep the level 2 heading');
  check(roster.querySelector('.status')?.textContent === 'Unknown' && !roster.querySelector('.status.exception'),
        'Unknown did not use the ordinary badge');
  check(statusLine.querySelector('time')?.dateTime === '2026-03-02T13:39:00Z', 'The refresh did not show the corrected observation time');
"""
        with patch.object(quarterdeck, "workers_page", self.instrumented_page(quarterdeck.workers_page, steps)), \
                patch.object(quarterdeck, "run_snapshot", advance_fixture):
            outcome, stderr = self.browser_page(port, budget=65000)
        self.assertEqual(outcome, "PASS", stderr)

    def test_an_expired_saved_reading_is_replaced_on_entry(self) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the workers browser test")
        port = self.start("/qd", workers_home=self.make_home([snapshot_task("Wren")]))
        steps = """
  const refreshed = settled();
  await new Promise(resolve => {
    const watcher = new MutationObserver(() => {
      if (roster.textContent.includes('Wren')) { watcher.disconnect(); resolve(); }
    });
    watcher.observe(roster, {childList: true});
  });
  check(statusLine.textContent.startsWith('Refreshing workers.') &&
        statusLine.querySelector('time')?.dateTime === '2026-03-02T13:40:00Z' &&
        button.getAttribute('aria-disabled') === 'true', 'The saved time and refresh progress must appear together');
  await refreshed;
  check(requestCount('workers-saved.json') === 1 && requestCount('workers.json?reuse=1') === 1 &&
        requestCount('workers.json') === 0, 'Entry did not request a single automatic check');
  check(!roster.textContent.includes('Wren') && roster.textContent.includes('Bracken'),
        'The expired roster was not replaced');
  check(statusLine.textContent.startsWith('Last checked ') && statusLine.querySelector('time')?.dateTime === '2026-03-02T14:45:00Z',
        'The completed refresh did not show its new time');
"""
        with patch.object(quarterdeck, "workers_clock", return_value=100) as clock:
            self.assertEqual(self.workers(port)[0], 200)
            clock.return_value += 3601
            self.make_home(body=json.dumps({"generated": "2026-03-02T14:45:00Z", "tasks": [snapshot_task("Bracken")]}))
            with patch.object(quarterdeck, "workers_page", self.instrumented_page(quarterdeck.workers_page, steps)), \
                    patch.object(quarterdeck, "run_snapshot", wraps=quarterdeck.run_snapshot) as run_snapshot:
                outcome, stderr = self.browser_page(port)
                run_snapshot.assert_called_once()
        self.assertEqual(outcome, "PASS", stderr)

    def test_a_fresh_saved_reading_is_shown_without_a_fetch_until_the_button(self) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the workers browser test")
        port = self.start("/qd", workers_home=self.make_home([snapshot_task("Wren")]))
        self.assertEqual(self.workers(port)[0], 200)  # saves the reading
        steps = """
  await quiet();
  check(requestCount('workers-saved.json') === 1, 'Expected one saved read on entry');
  check(requestCount('workers.json') === 0 && requestCount('workers.json?reuse=1') === 0,
        'A fresh saved reading was fetched again');
  check(roster.textContent.includes('Wren'), 'The saved roster did not appear');
  check(statusLine.textContent.startsWith('Last checked ') && statusLine.querySelector('time')?.dateTime === '2026-03-02T13:40:00Z',
        'The fresh saved reading did not show its settled time');
  const checked = settled();
  button.click();
  check(statusLine.textContent === 'Refreshing workers', 'The manual check did not show its progress');
  await checked;
  check(requestCount('workers.json') === 1 && requestCount('workers.json?reuse=1') === 0,
        'The check button did not make exactly one worker request');
"""
        with patch.object(quarterdeck, "workers_page", self.instrumented_page(quarterdeck.workers_page, steps)):
            outcome, stderr = self.browser_page(port)
        self.assertEqual(outcome, "PASS", stderr)

    def test_overlapping_workers_pages_receive_the_same_refresh(self) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the workers browser test")
        home = self.make_home([snapshot_task("Wren")])
        port = self.start("/qd", workers_home=home)
        first_started, second_entered, second_returned = (threading.Event() for _ in range(3))
        run_snapshot = quarterdeck.run_snapshot
        refresh = quarterdeck.SavedWorkers.refresh

        def hold_snapshot(home):
            first_started.set()
            self.assertTrue(second_entered.wait(timeout=5), "the second page never requested a refresh")
            # Keep the first check active until a rejecting handler returns or a waiting handler can join.
            second_returned.wait(timeout=0.5)
            return run_snapshot(home)

        def observe_refresh(saved, home, *, reuse):
            overlapping = first_started.is_set()
            if overlapping:
                second_entered.set()
            try:
                return refresh(saved, home, reuse=reuse)
            finally:
                if overlapping:
                    second_returned.set()

        steps = """
  if (window !== window.top) return;
  const other = document.createElement('iframe');
  const otherSettled = new Promise(resolve => {
    other.addEventListener('load', () => {
      const otherButton = other.contentDocument.getElementById('refresh-workers');
      if (!otherButton.hasAttribute('aria-disabled')) { resolve(); return; }
      const watcher = new MutationObserver(() => {
        if (!otherButton.hasAttribute('aria-disabled')) { watcher.disconnect(); resolve(); }
      });
      watcher.observe(otherButton, {attributes: true});
    }, {once: true});
  });
  other.src = location.href;
  document.body.append(other);
  await Promise.all([settled(), otherSettled]);
  for (const [label, page] of [['first', document], ['second', other.contentDocument]]) {
    const list = page.getElementById('workers-list');
    const status = page.getElementById('workers-status');
    check(list.textContent.includes('Bracken') && !list.textContent.includes('Wren') &&
          status.querySelector('time')?.dateTime === '2026-03-02T14:45:00Z',
          'Expected the refreshed roster and time in the ' + label + ' view. ' + status.textContent);
  }
"""
        with patch.object(quarterdeck, "workers_clock", return_value=100) as clock:
            self.assertEqual(self.workers(port)[0], 200)
            clock.return_value += 3601
            (home / "snapshot.json").write_text(json.dumps({
                "generated": "2026-03-02T14:45:00Z", "tasks": [snapshot_task("Bracken")],
            }))
            with patch.object(quarterdeck, "workers_page", self.instrumented_page(quarterdeck.workers_page, steps)), \
                    patch.object(quarterdeck.SavedWorkers, "refresh", observe_refresh), \
                    patch.object(quarterdeck, "run_snapshot", side_effect=hold_snapshot) as snapshot:
                outcome, stderr = self.browser_page(port)
                self.assertEqual(outcome, "PASS", stderr)
                snapshot.assert_called_once()

    def test_failed_saved_lookup_waits_for_a_manual_check(self) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the workers browser test")
        port = self.start("/qd", workers_home=self.make_home([snapshot_task("Wren")]))
        steps = """
  await settled();
  check(statusLine.textContent.includes('Workers could not be loaded'), 'The saved lookup failure was not shown');
  check(requestCount('workers.json') === 0 && requestCount('workers.json?reuse=1') === 0,
        'Entry checked the fleet after a failed saved lookup');
  const checked = settled();
  button.click();
  await checked;
  check(requestCount('workers.json') === 1 && requestCount('workers.json?reuse=1') === 0 && roster.textContent.includes('Wren'),
        'A manual check did not recover from the failed lookup');
"""
        page = self.instrumented_page(quarterdeck.workers_page, steps)

        def failed_saved_page(*args, **kwargs):
            return page(*args, **kwargs).replace("</head>", """<script>
const realFetch = window.fetch.bind(window);
window.fetch = (path, options) => realFetch(path === 'workers-saved.json' ? 'unavailable.json' : path, options);
</script></head>""", 1)

        with patch.object(quarterdeck, "workers_page", failed_saved_page):
            outcome, stderr = self.browser_page(port)
        self.assertEqual(outcome, "PASS", stderr)

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
            self.assertIn('<a href="workers.html">Workers</a>', page)
            self.assertIn('id="refresh-workers">Check workers</button>', self.fetch(port, "/qd/workers.html")[1])
            status, body, _ = self.fetch(port, "/qd/workers.json")
            self.assertEqual(status, 200)
            self.assertEqual([worker["name"] for worker in json.loads(body)["workers"]], ["Wren"])


class ServiceAndHelpTests(StoreCase):
    def test_serve_warns_that_reachable_clients_can_start_conversations(self) -> None:
        for host in ("127.0.0.1", "192.0.2.10"):
            with self.subTest(host=host), patch.object(quarterdeck, "make_server") as make_server:
                make_server.return_value.server_address = (host, 8765)
                code, _, warning = self.run_cli("serve", "--host", host)
                self.assertEqual(code, 0, warning)
                self.assertEqual("start conversations" in warning, host == "192.0.2.10")
                self.assertEqual("read or check the worker and second mate roster" in warning, host == "192.0.2.10")
        help_output = io.StringIO()
        with redirect_stdout(help_output), self.assertRaises(SystemExit) as exit_info:
            quarterdeck.main(["serve", "--help"])
        self.assertEqual(exit_info.exception.code, 0)
        self.assertIn("With firstmate_home set, they can also send asks, start conversations",
                      " ".join(help_output.getvalue().split()))

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

    def test_render_reserves_image_directories_before_the_first_pictured_add(self) -> None:
        state, external = quarterdeck.store_path().parent, self.root / "site"
        for directory in (state, external):
            for name in ("images", "reply-images", "Reply-Images"):
                with self.subTest(directory=directory, name=name):
                    output = directory / name
                    code, _, err = self.run_cli("render", "--output", str(output))
                    if directory == external and name != "images":
                        self.assertEqual((code, err), (0, ""))
                        self.assertTrue(output.is_file())
                    else:
                        self.assertEqual(code, 2)
                        self.assertIn(f"the Saved filename is reserved for {name.lower()}", err)
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


class ConversationTests(ImageCase):
    """Asks sent from a card, replies written from the command line, and the Replied state between them."""

    start = ServeTests.start
    fetch = ServeTests.fetch

    def post(self, port, path, payload, origin=None):
        base = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(base + path, method="POST", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "Origin": base if origin is None else origin})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error) if error.headers.get_content_type() == "application/json" else error.read().decode()

    def ask_body(self, port, part, text):
        item, _, _ = part.partition(".")
        page = self.fetch(port, f"/qd/{item.lower()}.html")[1]
        match = re.search(r'class="ask-button" data-home="([^"]+)"', page)
        return {"part": part, "id": uuid.uuid4().hex, "text": text, "home": match[1] if match else ""}

    def ask(self, port, part, text):
        return self.post(port, "/qd/asks", self.ask_body(port, part, text))

    def inbox(self, accept=True, name="home") -> Path:
        home = self.root / name
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('#!/bin/sh\nprintf "%s\\n" "$3" >> "$FM_HOME/attempts"\n'
                          + ('printf "%s\\n" "$6" >> "$FM_HOME/notes"\n' if accept else 'exit 1\n'))
        script.chmod(0o700)
        return home

    def messages(self, part="1", item=2):
        return self.store()["items"][item].get("conversation", {}).get(part, [])

    def setUp(self) -> None:
        super().setUp()
        self.add_all_kinds()
        self.add("questions", "Two decisions", "Both are open.", "--body-file", "-", stdin=json.dumps(QUESTIONS))

    def test_an_ask_keeps_the_question_open_and_reaches_the_inbox_marked_as_an_ask(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        before = self.store()["items"][3].get("answers")
        status, result = self.ask(port, "Q4.1", "Show me mockups,\nnot just text")
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"thread", "message"})
        self.assertEqual(result["message"], "Ask sent.")
        self.assertIn("Sent", result["thread"])
        self.assertIn("Show me mockups,\nnot just text", result["thread"])
        (message,) = self.messages("1", 3)
        self.assertEqual((message["author"], message["delivery"]), ("you", "sent"))
        self.assertEqual((home / "notes").read_text(), f"ASK Q4.1 {message['id']} Show me mockups,\nnot just text\n")
        self.assertEqual(self.store()["items"][3].get("answers"), before)
        self.assertIn("Open (2 of 2 open)", self.run_cli("show", "Q4")[1])

    def test_an_undelivered_ask_can_be_retried_with_the_same_note(self) -> None:
        home = self.inbox(accept=False)
        port = self.start("/qd", ask_home=home)
        status, result = self.ask(port, "Q4.2", "Which blue?")
        self.assertEqual((status, result["message"]), (200, "Ask saved. Not delivered."))
        self.assertIn("Not delivered", result["thread"])
        (message,) = self.messages("2", 3)
        retry = {"part": "Q4.2", "id": message["id"], "action": "retry"}
        self.assertEqual(self.post(port, "/qd/asks", retry)[1]["message"], "Ask saved. Not delivered.")
        self.inbox()
        self.assertEqual(self.post(port, "/qd/asks", retry)[1]["message"], "Ask sent.")
        self.assertEqual(self.post(port, "/qd/asks", retry)[0], 200)
        self.assertEqual((home / "notes").read_text(), f"ASK Q4.2 {message['id']} Which blue?\n")
        self.assertEqual((home / "attempts").read_text().splitlines(), [message["request_id"]] * 3)
        self.assertEqual(self.messages("2", 3), [{**message, "delivery": "sent"}])

    def test_create_replays_keep_the_same_ask_and_deliver_only_once(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        body = self.ask_body(port, "Q4.1", "Show the preview.")
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.post(port, "/qd/asks", body), range(2)))
        self.assertEqual([status for status, _ in responses], [200, 200])
        (message,) = self.messages("1", 3)
        self.assertEqual((message["id"], message["delivery"]), (body["id"], "sent"))
        self.assertEqual(self.post(port, "/qd/asks", body)[1]["message"], "Ask sent.")
        self.assertEqual(self.post(port, "/qd/asks", {**body, "text": "Different text."})[0], 409)
        self.assertEqual(self.post(port, "/qd/asks", {**body, "part": "Q4.2"})[0], 409)
        self.assertEqual((home / "notes").read_text(), f"ASK Q4.1 {body['id']} Show the preview.\n")
        self.assertEqual((home / "attempts").read_text().splitlines(), [message["request_id"]])

    def test_explicit_recovery_delivers_the_original_saved_ask(self) -> None:
        home = self.inbox(accept=False)
        port = self.start("/qd", ask_home=home)
        body = self.ask_body(port, "Q4.1", "Show (https://example.com/preview).")
        self.assertEqual(self.post(port, "/qd/asks", body)[1]["message"], "Ask saved. Not delivered.")
        (original,) = self.messages("1", 3)
        self.inbox()
        self.assertEqual(self.post(port, "/qd/asks", body)[1]["message"], "Ask saved. Not delivered.")
        recovery = {**body, "action": "retry"}
        self.assertEqual(self.post(port, "/qd/asks", {**recovery, "text": "Different preview."})[0], 409)
        status, result = self.post(port, "/qd/asks", recovery)
        self.assertEqual((status, result["message"]), (200, "Ask sent."))
        self.assertIn('<a href="https://example.com/preview" target="_blank"', result["thread"])
        self.assertEqual(self.post(port, "/qd/asks", recovery)[1]["message"], "Ask sent.")
        self.assertEqual((home / "notes").read_text(), f"ASK Q4.1 {body['id']} Show (https://example.com/preview).\n")
        self.assertEqual((home / "attempts").read_text().splitlines(), [original["request_id"]] * 2)
        self.assertEqual(self.messages("1", 3), [{**original, "delivery": "sent"}])

    def test_recovery_can_deliver_when_the_original_request_never_arrived(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        body = self.ask_body(port, "Q4.1", "Show the preview.")
        self.assertEqual(self.post(port, "/qd/asks", {**body, "action": "retry"})[1]["message"], "Ask sent.")
        self.assertEqual(self.post(port, "/qd/asks", body)[1]["message"], "Ask sent.")
        self.assertEqual((home / "notes").read_text(), f"ASK Q4.1 {body['id']} Show the preview.\n")

    def test_pending_asks_cannot_follow_a_configuration_change(self) -> None:
        original = self.inbox(accept=False)
        replacement = self.inbox(name="replacement")
        config = self.root / "explicit.json"
        config.write_text(json.dumps({"firstmate_home": str(original)}))
        with self.serve_cli("--config", str(config), "--base-path", "/qd") as port:
            body = self.ask_body(port, "Q4.1", "Private ask.")
            stale_draft = self.ask_body(port, "Q4.1", "A second private ask.")
            self.assertEqual(self.post(port, "/qd/asks", body)[1]["message"], "Ask saved. Not delivered.")
            self.assertEqual(self.post(port, "/qd/asks", body)[1]["message"], "Ask saved. Not delivered.")
            self.assertEqual(len((original / "attempts").read_text().splitlines()), 1)
        config.write_text(json.dumps({"firstmate_home": str(replacement)}))
        before = self.store()
        with self.serve_cli("--config", str(config), "--base-path", "/qd") as port:
            retry = {"part": body["part"], "id": body["id"], "action": "retry"}
            rebound = {**body, "home": self.ask_body(port, "Q4.1", "Unused")["home"]}
            for request in (retry, body, rebound, stale_draft, {**body, "action": "retry"}, {**rebound, "action": "retry"}):
                self.assertEqual(self.post(port, "/qd/asks", request),
                                 (409, {"message": "Firstmate home changed. This ask belongs to the original home.",
                                        "saved": request["id"] == body["id"]}))
            self.assertEqual(self.store(), before)
            self.assertFalse((replacement / "attempts").exists())

    def test_question_and_whole_item_asks_can_wait_for_a_missing_home_directory(self) -> None:
        home = self.root / "home"
        port = self.start("/qd", ask_home=home)
        page = self.fetch(port, "/qd/q4.html")[1]
        self.assertIn('id="open-item-ask"', page)
        self.assertIn('class="ask-button" data-home=', page)
        self.assertNotIn('id="open-talk"', self.fetch(port, "/qd/")[1])
        for target in ("Q4", "Q4.1"):
            with self.subTest(target=target):
                body = self.ask_body(port, target, f"Please explain {target}.")
                for request in (body, {**body, "action": "retry"},
                                {"part": target, "id": body["id"], "action": "retry"}):
                    status, result = self.post(port, "/qd/asks", request)
                    self.assertEqual((status, result["message"]), (200, "Ask saved. Not delivered."))
                    self.assertIn("Send ask again", result["thread"])
        whole, = self.messages("item", 3)
        numbered, = self.messages("1", 3)
        self.assertEqual((whole["delivery"], numbered["delivery"]), ("pending", "pending"))
        self.assertEqual(self.inbox(), home)
        for target, message in (("Q4", whole), ("Q4.1", numbered)):
            retry = {"part": target, "id": message["id"], "action": "retry"}
            self.assertEqual(self.post(port, "/qd/asks", retry)[1]["message"], "Ask sent.")
        self.assertEqual((home / "attempts").read_text().splitlines(), [whole["request_id"], numbered["request_id"]])

    def test_pending_asks_cannot_follow_a_repointed_home_symlink(self) -> None:
        original = self.inbox(accept=False)
        replacement = self.inbox(name="replacement")
        alias = self.root / "current"
        alias.symlink_to(original, target_is_directory=True)
        port = self.start("/qd", ask_home=alias)
        body = self.ask_body(port, "Q4.1", "Private ask.")
        self.assertEqual(self.post(port, "/qd/asks", body)[1]["message"], "Ask saved. Not delivered.")
        self.assertTrue((original / "attempts").is_file())
        retry = {"part": body["part"], "id": body["id"], "action": "retry"}
        alias.unlink()
        alias.symlink_to(replacement, target_is_directory=True)
        for request in (retry, body):
            self.assertEqual(self.post(port, "/qd/asks", request),
                             (409, {"message": "Firstmate home changed. This ask belongs to the original home.", "saved": True}))
        self.assertFalse((replacement / "attempts").exists())
        self.assertEqual(self.ask(port, "Q4.1", "A new ask.")[1]["message"], "Ask sent.")
        self.assertIn("A new ask.", (replacement / "notes").read_text())
        self.assertNotIn("Private ask.", (replacement / "notes").read_text())

    def test_legacy_home_can_receive_answers_but_cannot_receive_asks(self) -> None:
        home = self.inbox()
        config = self.root / "legacy.json"
        config.write_text(json.dumps({"homes": [{"path": "home"}]}))
        with self.serve_cli("--config", str(config), "--base-path", "/qd") as port:
            self.assertNotIn('class="ask-button"', self.fetch(port, "/qd/q4.html")[1])
            self.assertEqual(self.ask(port, "Q4.1", "Show the preview.")[0], 403)
            self.assertEqual(self.messages("1", 3), [])
            status, result = self.post(port, "/qd/answers", {"part": "Q4.1", "answer": "Hold", "revision": ""})
            self.assertEqual((status, result["delivery"]), (200, "sent"))
        self.assertEqual((home / "notes").read_text(), "Q4.1 Hold\n")

    def test_bad_asks_change_nothing(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        before = self.store()
        body = self.ask_body(port, "Q4.1", "Show me.")
        for payload in ({**body, "part": "Q4.3"}, {**body, "part": "Q1.1"}, {**body, "text": " "},
                        {**body, "text": "a" * 20001}, {**body, "extra": 1},
                        {"part": "Q4.1", "id": body["id"], "action": "other"},
                        {**body, "id": "bad"}, {**body, "text": "bad\u0085text"}, []):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(port, "/qd/asks", payload)[0], 400)
        self.assertEqual(self.store(), before)
        self.assertEqual(self.post(port, "/qd/asks", body, origin="https://example.org")[0], 403)
        self.assertEqual(self.post(port, "/qd/asks", {**body, "part": "Q9.1"})[0], 400)
        self.run_cli("mark", "Q4.1", "closed")
        before = self.store()
        self.assertEqual(self.post(port, "/qd/asks", body)[0], 409)
        self.assertEqual(self.store(), before)

    def test_an_ask_on_an_answered_question_leaves_the_answer_alone(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        base = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(base + "/qd/answers", method="POST", data=json.dumps(
            {"part": "Q4.1", "answer": "Merge", "revision": ""}).encode(),
            headers={"Content-Type": "application/json", "Origin": base})
        answered = json.load(urllib.request.urlopen(request, timeout=5))
        self.ask(port, "Q4.1", "Why merge?")
        self.assertEqual(self.store()["items"][3]["answers"]["1"]["answer"], "Merge")
        self.assertEqual(self.store()["items"][3]["answers"]["1"]["revision"], answered["revision"])
        self.assertEqual(self.store()["items"][3]["answers"]["1"]["state"], "answered")

    def test_reply_adds_text_links_and_images_and_never_changes_the_answer(self) -> None:
        config = self.root / "explicit.json"
        home = self.inbox()
        config.write_text(json.dumps({"firstmate_home": str(home)}))
        with self.serve_cli("--config", str(config), "--base-path", "/qd") as port:
            self.assertIn('class="ask-button"', self.fetch(port, "/qd/q4.html")[1])
            self.assertEqual(self.ask(port, "Q4.1", "Show both layouts.")[0], 200)
        (ask,) = self.messages("1", 3)
        code, out, err = self.run_cli("reply", "q4.1", "--ask", ask["id"], "--text", "Here are both.", "--link-text", "Open the preview",
                                      "--url", "https://example.com/preview", "--image", str(self.red), "--alt", "A red quay",
                                      "--image", str(self.blue), "--alt", "A blue quay")
        self.assertEqual((code, out.strip(), err), (0, "Q4.1 Replied", ""))
        _, message = self.messages("1", 3)
        self.assertEqual(message["ask"], ask["id"])
        self.assertEqual((message["author"], message["read"], message["text"]), ("firstmate", False, "Here are both."))
        self.assertEqual(message["links"], [{"text": "Open the preview", "url": "https://example.com/preview"}])
        self.assertEqual([image["alt"] for image in message["images"]], ["A red quay", "A blue quay"])
        for image in message["images"]:
            self.assertTrue((quarterdeck.store_path().parent / "reply-images" / "q4" / image["file"]).is_file())
        self.assertNotIn("answers", self.store()["items"][3])
        shown = self.run_cli("show", "Q4")[1]
        self.assertIn("\nOpen (2 of 2 open)\n", shown)
        self.assertIn("\n      Open\n", shown)
        for text in ("Replied", "Show both layouts.", "Here are both.", "Open the preview", "A red quay"):
            self.assertNotIn(text, shown)
        self.assertEqual(self.run_cli("remove", "Q4")[0], 0)
        self.assertEqual(list((quarterdeck.store_path().parent / "reply-images" / "q4").iterdir()), [])

    def test_reply_image_names_the_blocking_file_without_changing_the_store(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        blocker = quarterdeck.store_path().parent / "reply-images"
        blocker.write_text("Earlier Saved page.", encoding="utf-8")
        for target, thread in (("Q4", "item"), ("Q4.1", "1")):
            with self.subTest(target=target):
                self.assertEqual(self.ask(port, target, "Show the layout.")[0], 200)
                ask, = self.messages(thread, 3)
                reply = ("reply", target, "--ask", ask["id"], "--text", "Here it is.")
                before = self.store()
                code, out, err = self.run_cli(*reply, "--image", str(self.red), "--alt", "Layout")
                self.assertEqual((code, out, err),
                                 (2, "", f"quarterdeck: Move the file {blocker} before attaching reply images.\n"))
                self.assertEqual(self.store(), before)
                self.assertEqual(blocker.read_text(encoding="utf-8"), "Earlier Saved page.")
                self.assertEqual(self.run_cli(*reply)[0], 0)

    def test_reply_rejects_bad_input_and_changes_nothing(self) -> None:
        before = self.store()
        cases = (
            (("Q4", "--text", "x"), "has no asks"), (("Q4.9", "--text", "x"), "no question"), (("Q1.1", "--text", "x"), "questions item"),
            (("Q9.1", "--text", "x"), "no item"), (("Q4.1", "--text", "x"), "has no asks"), (("Q4.1", "--text", " "), "must not be empty"),
            (("Q4.1", "--text", "x", "--link-text", "Only words"), "--url"),
            (("Q4.1", "--text", "x", "--link-text", "Bad", "--url", "ftp://example.com"), "http"),
            (("Q4.1", "--text", "x", "--image", str(self.red)), "--alt"),
            (("Q4.1", "--text", "x", "--image", str(self.pictures / "none.png"), "--alt", "Nothing"), "could not be read"),
            (("Q4.1", "--text", "x", "--image", str(self.red), "--alt", " "), "must not be empty"),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                code, _out, err = self.run_cli("reply", *argv, "--ask", "0" * 32)
                self.assertEqual(code, 2)
                self.assertIn(expected, err)
        self.assertEqual(self.store(), before)

    def test_a_reply_accepts_seven_links_and_images(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.assertEqual(self.ask(port, "Q4.1", "Show all seven previews.")[0], 200)
        (ask,) = self.messages("1", 3)
        args = ["reply", "Q4.1", "--ask", ask["id"], "--text", "Here are all seven."]
        for n in range(7):
            image = self.pictures / f"preview-{n}.png"
            image.write_bytes(tiny_png((n, 0, 0)))
            args.extend(["--link-text", f"Preview {n}", "--url", f"https://example.com/{n}",
                         "--image", str(image), "--alt", f"Mockup {n}"])
        self.assertEqual(self.run_cli(*args)[0], 0)
        page = self.fetch(port, "/qd/q4.html")[1]
        for n in range(7):
            self.assertIn(f'>Preview {n}</a>', page)
            self.assertIn(f'alt="Mockup {n}"', page)

    def test_replied_persists_across_reads_until_acknowledged(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.ask(port, "Q4.2", "Which blue?")
        self.assertEqual(self.run_cli("reply", "Q4.2", "--ask", self.messages("2", 3)[0]["id"], "--text", "Blue is the newer sign.")[0], 0)
        _, reply = self.messages("2", 3)
        for _ in range(2):
            desk = self.fetch(port, "/qd/")[1].split("<h1>Desk</h1>", 1)[1]
            self.assertIn('<td>2 of 2 open</td><td><span class="badge">Replied</span></td>', desk)
            self.assertIn('<tr class="work next"><th scope="row"><a class="card-link" href="q4.html#Q4.1"', desk)
            page = self.fetch(port, "/qd/q4.html")[1]
            self.assertRegex(page, r'id="Q4.2".*?<span class="badge">Replied</span>')
            self.assertIn('<p class="progress">Replied, 2 of 2 open</p>', page)
            self.assertIn("Blue is the newer sign.", page)
            self.assertFalse(self.messages("2", 3)[1]["read"])
        self.assertEqual(self.post(port, "/qd/replies/read", {"part": "Q4.2", "ids": [reply["id"]]}),
                         (200, {"replies": {reply["id"]: True}}))
        self.assertTrue(self.messages("2", 3)[1]["read"])
        self.assertIn('<p class="progress">2 of 2 open</p>', self.fetch(port, "/qd/q4.html")[1])
        self.assertNotIn('>Replied</span>', self.fetch(port, "/qd/")[1])

    def test_a_new_ask_an_answer_and_closing_preserve_unread_replies(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.ask(port, "Q4.1", "Why merge?")
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", self.messages("1", 3)[0]["id"], "--text", "First answer from me.")[0], 0)
        self.assertEqual(self.ask(port, "Q4.1", "And then?")[0], 200)
        self.assertIn('<span class="badge">Replied</span>', self.fetch(port, "/qd/q4.html")[1])
        self.assertEqual(self.post(port, "/qd/answers", {"part": "Q4.1", "answer": "Merge", "revision": ""})[0], 200)
        self.assertIn("\n      Answered\n      Answer: Merge (", self.run_cli("show", "Q4")[1])
        self.run_cli("mark", "Q4", "closed")
        _, page, headers = self.fetch(port, "/qd/q4.html")
        self.assertIn('<p class="progress">Replied</p>', page)
        shown = self.run_cli("show", "Q4")[1]
        self.assertIn("\nClosed\n", shown)
        self.assertIn("\n      Closed\n      Answer: Merge (", shown)
        self.assertEqual(headers.get("Content-Security-Policy"), "frame-ancestors 'none'")
        _, reply, _ = self.messages("1", 3)
        self.assertEqual(self.post(port, "/qd/replies/read", {"part": "Q4.1", "ids": [reply["id"]]}),
                         (200, {"replies": {reply["id"]: True}}))
        self.assertIn('<p class="progress">Closed</p>', self.fetch(port, "/qd/q4.html")[1])

    def test_a_replied_item_keeps_its_desk_order_and_primary_action(self) -> None:
        self.add("questions", "Newest decision", "A later one.", "--body-file", "-", stdin=json.dumps(QUESTIONS[:1]))
        port = self.start("/qd", ask_home=self.inbox())
        self.ask(port, "Q3.2", "How does it look?")
        self.assertEqual(self.run_cli("reply", "Q3.2", "--ask", self.messages("2", 2)[0]["id"], "--text", "Looked at it.")[0], 0)
        desk = self.fetch(port, "/qd/")[1].split("<h1>Desk</h1>")[1]
        links = re.findall(r'<a class="card-link" href="[^"]+"><span class="id">(Q\d+)</span> ([^<]+)</a>', desk)
        self.assertEqual(links, [("Q5", "Newest decision"), ("Q4", "Two decisions"), ("Q3", "Tide launch decisions")])
        self.assertEqual(desk.count('<tr class="work next"'), 1)
        self.assertIn('<tr class="work next"><th scope="row"><a class="card-link" href="q5.html#Q5.1"', desk)
        self.assertIn('<tr class="work"><th scope="row"><a class="card-link" href="q3.html#Q3.1"', desk)

    def test_reply_and_pending_ask_leave_answered_items_in_the_library(self) -> None:
        port = self.start("/qd", ask_home=self.inbox(accept=False))
        self.assertEqual(self.run_cli("mark", "Q3", "answered", "--via", "chat")[0], 0)
        self.assertEqual(self.run_cli("mark", "Q4", "answered", "--via", "chat")[0], 0)
        self.ask(port, "Q4.2", "Anything else?")
        self.assertEqual(self.run_cli("reply", "Q4.2", "--ask", self.messages("2", 3)[0]["id"], "--text", "One more thing.")[0], 0)
        self.ask(port, "Q3.2", "Do you have a preview?")
        desk, library = self.fetch(port, "/qd/")[1].split('<dialog', 1)
        self.assertIn("Nothing is on your Desk", desk)
        answered = library.split('id="panel-answered"', 1)[1]
        self.assertRegex(answered, r'href="q4\.html"[^<]*</a></div><p class="meta"><span class="badge">Replied</span>')
        self.assertRegex(answered, r'href="q3\.html"[^<]*</a></div><p class="meta">Answered</p>')

    def test_whole_item_replies_are_private_and_acknowledged_separately(self) -> None:
        home = self.inbox(accept=False)
        port = self.start("/qd", ask_home=home)
        whole = self.ask_body(port, "Q4", "Show the whole layout.")
        self.assertEqual(self.post(port, "/qd/asks", whole)[1]["message"], "Ask saved. Not delivered.")
        original = self.messages("item", 3)[0]
        self.inbox()
        self.assertEqual(self.post(port, "/qd/asks", {**whole, "action": "retry"})[1]["message"], "Ask sent.")
        self.assertEqual((home / "notes").read_text(), f"ASK Q4 {whole['id']} Show the whole layout.\n")
        self.assertEqual((home / "attempts").read_text().splitlines(), [original["request_id"]] * 2)
        part = self.ask_body(port, "Q4.1", "Show the individual option.")
        self.assertEqual(self.post(port, "/qd/asks", part)[0], 200)
        self.assertEqual(self.run_cli("reply", "Q4", "--ask", part["id"], "--text", "Wrong scope.")[0], 2)
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", whole["id"], "--text", "Wrong scope.")[0], 2)
        self.assertEqual(self.run_cli("reply", "q4", "--ask", whole["id"], "--text", "Whole layout.",
                                      "--image", str(self.red), "--alt", "Whole preview.")[0], 0)
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", part["id"], "--text", "Individual option.")[0], 0)
        _, whole_reply = self.messages("item", 3)
        _, part_reply = self.messages("1", 3)
        page = self.fetch(port, "/qd/q4.html")[1]
        item_html, question_html = page.split('id="Q4.1"', 1)
        self.assertIn("Whole layout.", item_html)
        self.assertNotIn("Whole layout.", question_html)
        self.assertIn("Individual option.", question_html)
        image = whole_reply["images"][0]["file"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/qd/reply-images/q4/{image}", timeout=5) as response:
            self.assertEqual(response.read(), self.red.read_bytes())
        endpoint = "/qd/replies/read"
        self.assertEqual(self.post(port, endpoint, {"part": "Q4", "ids": [part_reply["id"]]})[0], 400)
        self.assertEqual(self.post(port, endpoint, {"part": "Q4.1", "ids": [whole_reply["id"]]})[0], 400)
        self.assertEqual(self.post(port, endpoint, {"part": "Q4.1", "ids": [part_reply["id"]]})[0], 200)
        self.assertIn('<p class="progress">Replied, 2 of 2 open</p>', self.fetch(port, "/qd/q4.html")[1])
        self.assertIn('<span class="badge">Replied</span>', self.fetch(port, "/qd/")[1])
        self.assertFalse(self.messages("item", 3)[1]["read"])
        export = self.root / "site" / "index.html"
        self.assertEqual(self.run_cli("render", "--output", str(export))[0], 0)
        self.assertNotIn("Whole layout.", (export.parent / "q4.html").read_text())
        self.assertNotIn("Replied", export.read_text())
        self.assertFalse((export.parent / "reply-images").exists())
        self.assertEqual(self.post(port, endpoint, {"part": "Q4", "ids": [whole_reply["id"]]}),
                         (200, {"replies": {whole_reply["id"]: True}}))
        self.assertIn('<p class="progress">2 of 2 open</p>', self.fetch(port, "/qd/q4.html")[1])
        self.assertNotIn('<span class="badge">Replied</span>', self.fetch(port, "/qd/")[1])
        self.assertNotIn("answers", self.store()["items"][3])
        damaged = self.store()
        damaged["items"][3]["conversation"]["item"][1]["ask"] = part["id"]
        quarterdeck.store_path().write_text(json.dumps(damaged))
        self.assertEqual(self.fetch(port, "/qd/q4.html")[0], 500)

    def test_reference_items_cannot_have_conversations(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        question_ask = self.ask_body(port, "Q4", "Show the whole layout.")
        before = self.store()
        for target in ("Q1", "Q2", "Q1.1", "Q2.1"):
            with self.subTest(target=target):
                _, page, headers = self.fetch(port, f"/qd/{target.split('.')[0].lower()}.html")
                self.assertNotIn('id="open-item-ask"', page)
                self.assertNotIn("Content-Security-Policy", headers)
                ask = {**question_ask, "part": target}
                for payload in (ask, {**ask, "action": "retry"},
                                {"part": target, "id": ask["id"], "action": "retry"}):
                    self.assertEqual(self.post(port, "/qd/asks", payload)[0], 400)
                self.assertEqual(self.post(port, "/qd/replies/read", {"part": target, "ids": [ask["id"]]})[0], 400)
                code, _, error = self.run_cli("reply", target, "--ask", ask["id"], "--text", "Here it is.")
                self.assertEqual(code, 2)
                self.assertIn("Choose a questions item.", error)
        self.assertEqual(self.store(), before)
        self.assertFalse((home / "attempts").exists())

        self.assertEqual(self.post(port, "/qd/asks", question_ask)[0], 200)
        valid = self.store()
        for index in (0, 1):
            with self.subTest(damaged_item=index):
                data = json.loads(json.dumps(valid))
                data["items"][index]["conversation"] = data["items"][3].pop("conversation")
                quarterdeck.store_path().write_text(json.dumps(data))
                self.assertEqual(self.fetch(port, "/qd/")[0], 500)

    def test_closed_items_keep_existing_threads_without_ask_controls(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        for target, key in (("Q4", "item"), ("Q4.1", "1")):
            with self.subTest(target=target):
                self.assertEqual(self.run_cli("mark", "Q4", "open")[0], 0)
                body = self.ask_body(port, target, "Explain this decision.")
                stale = self.ask_body(port, target, "Another question.")
                changing_store = quarterdeck.changing_store

                @contextmanager
                def close_before_delivery(path):
                    with patch.object(quarterdeck, "changing_store", changing_store):
                        self.assertEqual(self.run_cli("mark", "Q4", "closed")[0], 0)
                    with changing_store(path) as data:
                        yield data

                with patch.object(quarterdeck, "changing_store", close_before_delivery):
                    status, conflict = self.post(port, "/qd/asks", body)
                self.assertEqual((status, conflict["saved"]), (409, True))
                original = self.messages(key, 3)[0]
                self.assertEqual((original["id"], original["delivery"]), (body["id"], "pending"))
                for payload, saved in ((stale, False), ({**stale, "action": "retry"}, False),
                                       ({**body, "action": "retry"}, True),
                                       ({"part": target, "id": body["id"], "action": "retry"}, True)):
                    status, conflict = self.post(port, "/qd/asks", payload)
                    self.assertEqual((status, conflict["saved"]), (409, saved))
                self.assertEqual(self.messages(key, 3), [original])
                self.assertEqual(self.run_cli("reply", target, "--ask", body["id"], "--text", "Here it is.")[0], 0)
                status, page, headers = self.fetch(port, "/qd/q4.html")
                self.assertEqual(status, 200)
                self.assertIn('Here it is.', page)
                for absent in ('id="open-item-ask"', 'id="item-ask-editor"', '<button id="send"', 'data-retry-ask='):
                    self.assertNotIn(absent, page)
                self.assertEqual(headers.get("Content-Security-Policy"), "frame-ancestors 'none'")
                reply = self.messages(key, 3)[1]
                self.assertEqual(self.post(port, "/qd/replies/read", {"part": target, "ids": [reply["id"]]})[0], 200)
        self.assertFalse((home / "attempts").exists())

    def test_static_exports_exclude_conversation_and_reply_images(self) -> None:
        port = self.start("/qd", ask_home=self.inbox(accept=False))
        self.ask(port, "Q4.1", "Private ask.")
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", self.messages("1", 3)[0]["id"], "--text", "Private reply.", "--image", str(self.red),
                                      "--alt", "Private mockup.")[0], 0)
        self.add("questions", "An illustrated question", "Public preview.", "--body-file", "-", stdin=json.dumps([
            {**QUESTIONS[0], "images": [{"path": str(self.blue), "alt": "Saved preview"}]},
        ]))
        isolated = self.root / "site" / "index.html"
        state = quarterdeck.store_path().parent
        for output, args in ((state / "index.html", ()), (isolated, ("--output", str(isolated)))):
            self.assertEqual(self.run_cli("render", *args)[0], 0)
            for page in (output, output.parent / "q4.html"):
                content = page.read_text()
                for private in ("Private ask.", "Private reply.", "Private mockup.", ">Replied<", ">Not delivered<"):
                    self.assertNotIn(private, content)
            packaged = sorted(path.read_bytes() for path in (output.parent / "images").glob("*/*"))
            self.assertEqual(packaged, [self.blue.read_bytes()])
        self.assertFalse((isolated.parent / "reply-images").exists())
        self.assertEqual(self.run_cli("render", "--output", str(state / "reply-images" / "q4" / "index.html"))[0], 2)
        self.assertFalse(self.messages("1", 3)[1]["read"])

    def test_overlapping_asks_show_all_replies_under_the_correct_ask(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for text in ("Show red.", "Show blue.", "Show green."):
            self.assertEqual(self.ask(port, "Q4.1", text)[0], 200)
        red, blue, green = [m["id"] for m in self.messages("1", 3)]
        self.ask(port, "Q4.2", "Show another layout.")
        other = self.messages("2", 3)[0]["id"]
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", other, "--text", "No match.")[0], 2)
        for ask, text in ((blue, "Blue mockup."), (red, "Red mockup.")):
            self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", ask, "--text", text)[0], 0)
        before = self.messages("1", 3)
        with self.assertRaises(SystemExit) as missing:
            self.run_cli("reply", "Q4.1", "--text", "Red detail.")
        self.assertEqual(missing.exception.code, 2)
        self.assertEqual(self.messages("1", 3), before)
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", red, "--text", "Red detail.")[0], 0)
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", green, "--text", "Green mockup.")[0], 0)
        page = self.fetch(port, "/qd/q4.html")[1]
        groups = re.findall(r'<ol class="exchange">(.*?)</ol>', page, re.DOTALL)
        self.assertEqual([re.findall(r'<p class="record">(.*?)</p>', group) for group in groups], [
            ["Show red.", "Red mockup.", "Red detail."], ["Show blue.", "Blue mockup."],
            ["Show green.", "Green mockup."], ["Show another layout."],
        ])
        self.assertEqual(self.messages("1", 3)[-1]["ask"], green)

    def test_an_ask_past_one_hundred_messages_can_be_delivered_and_answered(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.ask(port, "Q4.1", "First ask.")
        (ask,) = self.messages("1", 3)
        for number in range(99):
            self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", ask["id"], "--text", f"Detail {number}.")[0], 0)
        self.assertEqual(self.ask(port, "Q4.1", "One more ask.")[1]["message"], "Ask sent.")
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", self.messages("1", 3)[-1]["id"], "--text", "One more reply.")[0], 0)
        self.assertEqual(len(self.messages("1", 3)), 102)
        self.assertIn("One more reply.", self.fetch(port, "/qd/q4.html")[1])

    def test_reply_images_are_served_for_that_item_only_and_links_open_safely(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.ask(port, "Q4.1", "Show the preview.")
        self.run_cli("reply", "Q4.1", "--ask", self.messages("1", 3)[0]["id"], "--text", "See https://example.com/a.", "--link-text", "The preview",
                     "--url", "https://example.com/preview", "--image", str(self.red), "--alt", "A red quay")
        page = self.fetch(port, "/qd/q4.html")[1]
        file = self.messages("1", 3)[1]["images"][0]["file"]
        status, refreshed = self.ask(port, "Q4.1", "Can you show another layout?")
        self.assertEqual(status, 200)
        for markup in (page, refreshed["thread"]):
            self.assertIn(f'<a class="shot" href="reply-images/q4/{file}" target="_blank" rel="noopener">'
                          f'<img src="reply-images/q4/{file}" alt="A red quay"', markup)
            self.assertIn('<a class="ref-link" href="https://example.com/preview" target="_blank" rel="noopener noreferrer">The preview</a>', markup)
        self.assertIn('<a href="https://example.com/a" target="_blank"', page)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/qd/reply-images/q4/{file}", timeout=5) as response:
            self.assertEqual((response.status, response.headers["Content-Type"]), (200, "image/png"))
        self.assertEqual(self.fetch(port, f"/qd/reply-images/q3/{file}")[0], 404)
        self.assertEqual(self.fetch(port, f"/qd/images/q4/{file}")[0], 404)

    def test_messages_are_escaped(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.ask(port, "Q4.1", "What does it say?")
        self.run_cli("reply", "Q4.1", "--ask", self.messages("1", 3)[0]["id"], "--text", "<script>alert(1)</script>")
        self.ask(port, "Q4.2", "<img src=x onerror=alert(2)>")
        page = self.fetch(port, "/qd/q4.html")[1]
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_a_damaged_conversation_is_reported(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.ask(port, "Q4.1", "How is it?")
        self.run_cli("reply", "Q4.1", "--ask", self.messages("1", 3)[0]["id"], "--text", "Fine.")
        data = self.store()
        for damage in (lambda c: c["1"][1].update(read="yes"), lambda c: c["1"][1].update(author="owner"),
                       lambda c: c["1"][0].update(author=["you"]), lambda c: c["1"][1].update(author={"firstmate": True}),
                       lambda c: c.update({"9": c.pop("1")}), lambda c: c["1"][1].update(extra=1),
                       lambda c: c["1"][1].update(images=[{"file": "../x.png", "alt": "x"}]),
                       lambda c: c["1"][1].update(ask="0" * 32), lambda c: c["1"].reverse(),
                       lambda c: c.update({"1": []})):
            damaged = json.loads(json.dumps(data))
            damage(damaged["items"][3]["conversation"])
            with self.subTest(damage=damage):
                quarterdeck.store_path().write_text(json.dumps(damaged))
                status, _, error = self.run_cli("show", "Q4")
                self.assertEqual(status, 2)
                self.assertIn("the item store is damaged", error)

    def test_the_store_rejects_numeric_conversation_identifiers(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        ask = {**self.ask_body(port, "Q4.1", "How is it?"), "id": "1" * 32}
        self.assertEqual(self.post(port, "/qd/asks", ask)[0], 200)
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", ask["id"], "--text", "Fine.")[0], 0)
        data = self.store()
        for author, field, width in (("you", "id", 32), ("you", "home", 64), ("you", "request_id", 32),
                                     ("firstmate", "id", 32), ("firstmate", "ask", 32)):
            with self.subTest(author=author, field=field):
                damaged = json.loads(json.dumps(data))
                thread = damaged["items"][3]["conversation"]["1"]
                if author == "you" and field == "id":
                    thread.pop()
                message = next(record for record in thread if record["author"] == author)
                message[field] = int("1" * width)
                quarterdeck.store_path().write_text(json.dumps(damaged))
                status, body, _ = self.fetch(port, "/qd/q4.html")
                self.assertEqual(status, 500)
                self.assertIn("could not read its item store", body)

    def test_acknowledgment_is_same_origin_and_scoped_to_shown_replies_on_one_part(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for part in ("Q4.1", "Q4.2"):
            body = self.ask_body(port, part, "Show me.")
            self.assertEqual(self.post(port, "/qd/asks", body)[0], 200)
            self.assertEqual(self.run_cli("reply", part, "--ask", body["id"], "--text", "Here it is.")[0], 0)
        _, shown = self.messages("1", 3)
        _, sibling = self.messages("2", 3)
        self.assertIn("Here it is.", self.fetch(port, "/qd/q4.html")[1])
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", shown["ask"], "--text", "Arrived later.")[0], 0)
        payload = {"part": "Q4.1", "ids": [shown["id"]]}
        endpoint = "/qd/replies/read"
        self.assertEqual(self.post(port, endpoint, payload, origin="https://example.org")[0], 403)
        self.assertEqual(self.post(port, endpoint, {**payload, "ids": [shown["id"], sibling["id"]]})[0], 400)
        self.assertFalse(self.messages("1", 3)[1]["read"])
        _, _, later = self.messages("1", 3)
        self.assertEqual(self.post(port, endpoint, payload), (200, {"replies": {shown["id"]: True, later["id"]: False}}))
        self.assertEqual([m["read"] for m in self.messages("1", 3)[1:]], [True, False])
        self.assertFalse(self.messages("2", 3)[1]["read"])

    def check_page(self, port, script, *, setup="", fragment="", page=None, viewport_width=None):
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the ask-card browser tests")
        page = self.fetch(port, "/qd/q4.html")[1] if page is None else page
        page = page.replace("</head>", f"<script>{setup}</script></head>")
        fixture = self.root / "ask.html"
        page = page.replace("</body>", """
<output id="browser-result">Incomplete</output>
<script>
const browserResult = window.parent.document.getElementById('browser-result');
const check = (condition, message) => { if (!condition) throw new Error(message); };
const field = n => document.getElementById('answer-Q4.' + n);
const card = n => document.getElementById('Q4.' + n);
const type = (n, text) => { field(n).value = text; field(n).dispatchEvent(new Event('input', {bubbles: true})); };
const status = () => document.querySelector('main output[role="status"]');
const settled = () => new Promise(resolve => setTimeout(resolve, 50));
(async () => {
""" + script + """
  browserResult.textContent = 'PASS';
})().catch(error => { browserResult.textContent = error.message; });
</script>
</body>""")
        if viewport_width is not None:
            page = ('<!doctype html><output id="browser-result">Incomplete</output>'
                    f'<iframe width="{viewport_width}" height="844" srcdoc="{escape(page)}"></iframe>')
        fixture.write_text(page, encoding="utf-8")
        result = subprocess.run([chrome, "--headless", "--disable-background-networking", "--no-first-run", "--lang=en-US",
                                 f"--user-data-dir={self.root / 'browser'}", "--virtual-time-budget=4000", "--dump-dom", fixture.as_uri() + fragment],
                                env={**os.environ, "TZ": "Asia/Tokyo", "LC_ALL": "en_US.utf-8"},
                                capture_output=True, text=True, timeout=30, check=True)
        outcome = result.stdout.partition('<output id="browser-result">')[2].partition("</output>")[0]
        self.assertEqual(outcome, "PASS", result.stderr)

    def test_the_page_requires_a_new_ask_draft_and_preserves_the_reply_badge_on_answer(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.assertEqual(self.post(port, "/qd/answers", {"part": "Q4.1", "answer": "Merge", "revision": ""})[0], 200)
        self.check_page(port, r"""
const ask = n => card(n).querySelector('.ask-button');
const send = document.getElementById('send');
check(field(1).value === 'Merge' && ask(1).disabled && ask(2).disabled, 'Ask Firstmate accepted a saved answer');
check(card(1).contains(ask(1)) && !card(1).contains(send), 'Question and page actions moved');
check(!send.disabled, 'Pending answer could not be retried');
type(1, '  Merge  ');
check(ask(1).disabled, 'Whitespace on a saved answer enabled Ask Firstmate');
type(1, 'Show me mockups');
check(!ask(1).disabled && ask(2).disabled, 'Ask Firstmate did not follow its own box');
const calls = [];
window.fetch = async (url, options) => {
  calls.push([url, JSON.parse(options.body)]);
  const body = url === 'asks'
    ? {message: 'Ask sent.',
       thread: `<ol class="exchange"><li class="msg you" data-ask-id="${calls[0][1].id}"><p class="who"><strong>You</strong> <time datetime="2026-11-25T01:00:00Z">x</time> <span class="delivery">Sent</span></p><p class="record">Show me mockups</p></li><li class="msg firstmate" data-reply-id="8" data-unread>Mockup</li></ol>`}
    : {state: 'answered', revision: 'new', delivery: 'sent', answer: 'Hold', answered_at: '2026-11-25T01:00:00Z'};
  return {ok: true, status: 200, json: async () => body};
};
ask(1).click();
await settled();
check(calls.length === 1 && calls[0][0] === 'asks', 'The ask went to ' + calls.map(c => c[0]));
check(calls[0][1].part === 'Q4.1' && calls[0][1].text === 'Show me mockups' && !('answer' in calls[0][1]), 'Wrong ask body');
check(card(1).querySelector('.thread .record').textContent === 'Show me mockups', 'The ask did not appear');
check(/2026/.test(card(1).querySelector('.thread time').textContent) && /AM|PM/.test(card(1).querySelector('.thread time').textContent), 'The time was not localised');
check(card(1).querySelector('.badge').textContent === 'Replied', 'An ask cleared the Replied badge');
check(field(1).value === 'Merge' && ask(1).disabled, 'The saved answer was not restored');
check(status().textContent === 'Ask sent.', 'Status: ' + status().textContent);
type(1, 'Hold');
send.click();
await settled();
check(calls[1][0] === 'answers' && calls[1][1].answer === 'Hold', 'The answer was not sent');
check(card(1).querySelector('.badge').textContent === 'Replied', 'Saving an answer cleared the Replied badge');
check(document.querySelector('.progress').textContent === 'Replied, 1 of 2 open', 'Wrong progress');
check(field(1).value === 'Hold' && ask(1).disabled && send.disabled, 'The saved answer became a draft');
""")

    def test_the_page_retries_an_undelivered_ask(self) -> None:
        port = self.start("/qd", ask_home=self.inbox(accept=False))
        self.ask(port, "Q4.1", "Which one?")
        (message,) = self.messages("1", 3)
        retry_expected = json.dumps({"part": "Q4.1", "id": message["id"], "action": "retry"})
        self.check_page(port, r"""
const button = card(1).querySelector('[data-retry-ask]');
check(button.textContent === 'Send ask again', 'Retry action missing');
check(card(1).querySelector('.thread .delivery').textContent === 'Not delivered', 'No delivery state');
const calls = [];
window.fetch = async (url, options) => {
  calls.push([url, JSON.parse(options.body)]);
  return {ok: true, status: 200, json: async () => ({message: 'Ask sent.',
    thread: '<ol class="exchange"><li><span class="delivery">Sent</span><p class="record">Which one?</p></li></ol>'})};
};
card(1).querySelector('summary').click();
button.click();
await settled();
check(calls[0][0] === 'asks' && JSON.stringify(calls[0][1]) === JSON.stringify(""" + retry_expected + r"""), 'Wrong request');
check(card(1).querySelector('.thread .record').textContent === 'Which one?', 'Retry lost the ask');
check(card(1).querySelector('.thread .delivery').textContent === 'Sent', 'Retry did not update delivery');
check(!card(1).querySelector('[data-retry-ask]'), 'A delivered ask can still be retried');
""")

    def test_the_page_acknowledges_only_the_opened_question_even_when_closed(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for part in ("Q4.1", "Q4.2"):
            body = self.ask_body(port, part, "Show me.")
            self.assertEqual(self.post(port, "/qd/asks", body)[0], 200)
            self.assertEqual(self.run_cli("reply", part, "--ask", body["id"], "--text", "Here it is.")[0], 0)
        self.run_cli("mark", "Q4", "closed")
        _, selected = self.messages("2", 3)
        expected = json.dumps({"part": "Q4.2", "ids": [selected["id"]]})
        self.check_page(port, r"""
await settled();
check(calls.length === 0 && !card(2).querySelector('details').open, 'Loading with a question fragment acknowledged replies');
document.dispatchEvent(new Event('visibilitychange'));
location.hash = '#Q4.1';
await settled();
check(calls.length === 0, 'Navigation or visibility acknowledged replies');
card(2).querySelector('summary').click();
await settled();
check(calls.length === 1 && calls[0][0] === 'replies/read', 'Wrong acknowledgment endpoint or count');
check(JSON.stringify(calls[0][1]) === JSON.stringify(""" + expected + r"""), 'Wrong reply was acknowledged');
check(card(2).querySelector('details').open, 'Messages did not open');
check(card(2).querySelector('.badge').textContent === 'Closed', 'Opened question did not return to Closed');
check(card(1).querySelector('.badge').textContent === 'Replied', 'Sibling lost its Replied badge');
check(document.querySelector('.progress').textContent === 'Replied', 'Progress lost the sibling reply');
card(1).querySelector('summary').click();
await settled();
check(calls.length === 2 && calls[1][1].part === 'Q4.1', 'Opening the sibling did not acknowledge its reply');
check(document.querySelector('.progress').textContent === 'Closed', 'Progress did not return to Closed');
""", fragment="#Q4.2", setup="""
const calls = [];
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  calls.push([url, body]);
  return {ok: true, json: async () => ({replies: {[body.ids[0]]: true}})};
};
""")

    def test_interrupted_ask_stays_pending_while_a_sibling_answer_is_sent(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.check_page(port, r"""
const draft = 'Show (https://example.com/preview).';
const calls = [];
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  calls.push([url, body]);
  if (calls.length === 1) throw new Error('Connection lost');
  const result = url === 'answers'
    ? {state: 'answered', revision: 'new', delivery: 'sent', answer: 'Blue', answered_at: '2026-11-25T01:00:00Z'}
    : {message: 'Ask sent.', thread: `<ol><li data-ask-id="${body.id}"><p class="record">Show (<a href="https://example.com/preview">https://example.com/preview</a>).</p></li></ol>`};
  return {ok: true, status: 200, json: async () => result};
};
type(1, draft);
type(2, 'Blue');
card(1).querySelector('.ask-button').click();
const pending = card(1).querySelector('.pending-asks');
check(field(1).value === '' && pending.querySelector('.record').textContent === draft,
      'Ask stayed in the answer field or disappeared from the pending thread');
check(!pending.querySelector('a'), 'An unsaved ask linked a URL');
await settled();
check(/interrupted/.test(status().textContent), 'Interrupted state was not reported');
const send = document.getElementById('send');
check(send.textContent === 'Send 1 answer', 'The interrupted ask entered the answer batch');
send.click();
await settled();
check(calls.length === 2 && calls[1][0] === 'answers' && calls[1][1].part === 'Q4.2',
      'A sibling submission included the ask');
pending.querySelector('button').click();
await settled();
check(calls.length === 3 && calls[2][0] === 'asks' && /^[a-f0-9]{32}$/.test(calls[0][1].id)
      && JSON.stringify(calls[2][1]) === JSON.stringify({...calls[0][1], action: 'retry'}), 'Wrong recovery request');
const saved = card(1).querySelector('.thread-messages .record');
check(!pending.firstElementChild && saved.textContent === draft, 'Recovery lost or duplicated the original ask');
check(saved.querySelector('a').href === 'https://example.com/preview', 'The saved ask link was lost');
""")

    def test_toolbar_opens_an_independent_whole_item_editor(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        ask = self.ask_body(port, "Q4", "Show the whole layout.")
        self.assertEqual(self.post(port, "/qd/asks", ask)[0], 200)
        self.assertEqual(self.run_cli("reply", "Q4", "--ask", ask["id"], "--text", "Here is the whole layout.")[0], 0)
        _, reply = self.messages("item", 3)
        self.check_page(port, r"""
const calls = [];
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  calls.push([url, body]);
  const result = url === 'replies/read' ? {replies: {[replyId]: true}} :
    url === 'answers' ? {state: 'answered', revision: 'new', delivery: 'sent',
                        answer: 'Hold', answered_at: '2026-11-25T01:00:00Z'} :
    {thread: `<ol><li data-ask-id="${body.id}"><p class="record">Explain the colors.</p></li></ol>`,
     message: 'Ask sent.', delivery: 'sent'};
  return {ok: true, status: 200, json: async () => result};
};
const whole = document.getElementById('Q4');
const opener = document.getElementById('open-item-ask');
const editor = document.getElementById('item-ask-editor');
const fieldForItem = editor.querySelector('textarea');
const send = document.getElementById('send');
check(editor.hidden && opener.parentElement === send.parentElement, 'The page bar did not own the item editor');
opener.click();
check(!editor.hidden && document.activeElement === fieldForItem && calls.length === 0,
      'Opening the editor did not focus it or acknowledged the unread reply');
fieldForItem.value = 'Explain the colors.';
fieldForItem.dispatchEvent(new Event('input', {bubbles: true}));
check(send.disabled, 'The whole-item draft was queued as an answer');
type(1, 'Hold');
check(!card(1).querySelector('.ask-button').disabled && send.textContent === 'Send 1 answer',
      'The numbered-card actions were changed');
whole.querySelector('summary').click();
await settled();
check(calls[0][0] === 'replies/read' && calls[0][1].part === 'Q4' && calls[0][1].ids.join() === replyId,
      'Opening Messages acknowledged the wrong conversation');
check(whole.querySelector('.badge').hidden && document.querySelector('.progress').textContent === '2 of 2 open',
      'The item-level badge did not clear after acknowledgment');
document.getElementById('queue-add').click();
const queued = [...document.querySelectorAll('#queue-list li')].map(li => li.textContent);
check(queued.length === 2 && queued[0].includes('Answer \u00b7 Q4.1') && queued[0].includes('Hold') &&
      queued[1].includes('Set message') && queued[1].includes('Explain the colors.'),
      'The queue did not name the answer by question ID with its text beside the set message');
[...document.querySelectorAll('#queue-list button')].find(b => b.textContent === 'Remove from send').click();
check(field(1).value === 'Hold' && send.disabled && document.querySelectorAll('#queue-list li').length === 1,
      'Removing the answer from the send lost its draft');
document.getElementById('send-queue').click();
await settled();
check(calls[1][0] === 'asks' && calls[1][1].part === 'Q4' && calls[1][1].text === 'Explain the colors.',
      'The queued set message did not target the whole item');
check(fieldForItem.value === '' && whole.querySelector('.record').textContent === 'Explain the colors.' &&
      field(1).value === 'Hold' && calls.length === 2,
      'A message-only send changed the answer draft or was not shown in its thread');
card(1).querySelector('[data-choice="Hold"]').click();
send.click();
await settled();
check(calls.length === 3 && calls[2][0] === 'answers' && calls[2][1].part === 'Q4.1',
      'The batch did not remain scoped to the numbered question');
""", setup=f"const replyId = {json.dumps(reply['id'])};")

    SET_STUB = r"""
const calls = [];
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  calls.push([url, body]);
  const result = url === 'answers' ? {state: 'answered', revision: 'new', delivery: 'sent',
                                      answer: body.answer, answered_at: '2026-11-25T01:00:00Z'} :
    {thread: `<ol><li data-ask-id="${body.id}"><p class="record">${body.text}</p></li></ol>`,
     message: 'Ask sent.', delivery: 'sent'};
  return {ok: true, status: 200, json: async () => result};
};
const queueItems = () => [...document.querySelectorAll('#queue-list li')].map(li => li.textContent);
const queueText = text => {
  const box = document.getElementById('ask-Q4');
  box.value = text;
  box.dispatchEvent(new Event('input', {bubbles: true}));
  document.getElementById('queue-add').click();
};
document.getElementById('open-item-ask').click();
"""

    def test_set_discussion_sends_the_cover_answer_with_the_request_and_leaves_quantity_open(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.check_page(port, self.SET_STUB + r"""
check(document.getElementById('queue-empty').textContent === 'Your queue is empty.' &&
      document.getElementById('send-queue').disabled, 'An empty queue offered a send');
type(1, 'Hold');
queueText('Please send me the proof.');
const composer = document.getElementById('ask-Q4');
composer.value = 'Please send me the proof before ordering the run.';
composer.dispatchEvent(new Event('input', {bubbles: true}));
const items = queueItems();
const preview = [...document.querySelectorAll('#queue-list li:first-child p')].map(p => p.textContent);
check(items.length === 2 && preview.join('|') === 'Answer \u00b7 Q4.1|Hold' &&
      items[1].includes('Set message') && items[1].includes('Please send me the proof'),
      'Each queued entry did not name its question ID and show its text: ' + items.join(' / '));
check(document.getElementById('send-queue').textContent === 'Send queued items (2)', 'The queue count was wrong');
document.getElementById('send-queue').click();
await settled();
check(calls.length === 2 && calls[0][0] === 'answers' && calls[0][1].part === 'Q4.1' && calls[0][1].answer === 'Hold' &&
      calls[1][0] === 'asks' && calls[1][1].part === 'Q4' && calls[1][1].text === 'Please send me the proof before ordering the run.',
      'The answer and the request were not sent together: ' + JSON.stringify(calls));
check(card(1).dataset.state === 'answered' && card(2).dataset.state === 'open' && field(2).value === '' &&
      !calls.some(([, body]) => body.part === 'Q4.2'), 'The other question did not stay open');
check(status().textContent === 'Queued items sent.' && queueItems().length === 0, 'The queue did not empty');
check(composer.value === '', 'The sent draft remained in the composer');
""")

    def test_set_discussion_sends_only_the_request_and_keeps_the_cover_draft(self) -> None:
        fixture = self.store()
        fixture["items"][3]["body"]["questions"][1]["suggestion"] = "Red"
        quarterdeck.store_path().write_text(json.dumps(fixture))
        port = self.start("/qd", ask_home=self.inbox())
        self.check_page(port, self.SET_STUB + r"""
const hold = card(1).querySelector('[data-choice="Hold"]');
type(1, 'Hold');
card(1).querySelector('[data-choice="Merge"]').click();
queueText('Please send me the proof before ordering the run.');
check(queueItems().length === 2 && document.getElementById('send').textContent === 'Send 1 answer', 'The draft was not queued');
[...document.querySelectorAll('#queue-list button')].find(b => b.textContent === 'Remove from send').click();
document.getElementById('undo').click();
check(field(1).value === 'Hold' && queueItems().length === 1 && document.getElementById('send').disabled,
      'Undo lost the draft or reversed a later removal');
card(1).querySelector('[data-choice="Merge"]').click();
document.getElementById('undo').click();
check(field(1).value === 'Hold' && queueItems().length === 1 && document.getElementById('send').disabled,
      'Undo put the excluded answer back in both send paths');
type(1, 'Hold ');
check(queueItems().length === 1 && document.getElementById('send').disabled,
      'A whitespace-only edit requeued the removed answer');
type(1, 'Hold');
check(document.querySelectorAll('#queue-list button').length === 0,
      'The set-message preview offered a separate removal action');
queueText('   ');
check(queueItems().length === 0 && document.getElementById('send-queue').disabled,
      'Clearing the composer left the message queued');
queueText('Please send me the proof before ordering the run.');
document.getElementById('send-queue').click();
await settled();
check(calls.length === 1 && calls[0][0] === 'asks' && calls[0][1].part === 'Q4', 'A message-only send also sent an answer');
check(card(1).dataset.state === 'open' && card(2).dataset.state === 'open' && field(1).value === 'Hold',
      'A message-only send closed a question or lost the cover draft');
hold.click();
check(!document.getElementById('send').disabled && queueItems().length === 1 && queueItems()[0].includes('Q4.1'),
      'Reselecting the checked option did not return it to the send');
document.getElementById('undo').click();
check(field(1).value === 'Hold' && document.getElementById('send').disabled &&
      document.getElementById('send-queue').disabled, 'Undo did not restore the exclusion');
type(1, '');
field(1).focus();
check(document.execCommand('insertText', false, 'Wait until we see the proof') &&
      !document.getElementById('send').disabled, 'A replacement answer was not queued');
check(document.execCommand('undo') && field(1).value === '' &&
      document.getElementById('send').disabled && document.getElementById('send-queue').disabled,
      'Undo did not restore the excluded blank field');
document.getElementById('suggest').click();
check(field(1).value === '' && field(2).value === 'Red' && queueItems().length === 1 &&
      queueItems()[0].includes('Q4.2') && document.getElementById('send').textContent === 'Send 1 answer',
      'Bulk suggestions filled or requeued an excluded question');
document.getElementById('undo').click();
check(field(2).value === '' && document.getElementById('send').disabled &&
      document.getElementById('send-queue').disabled, 'Undo did not restore the queue after bulk suggestions');
""")

    def test_typing_in_a_removed_custom_answer_requeues_it(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.check_page(port, self.SET_STUB + r"""
const original = 'Wait for the first proof';
type(1, original);
document.querySelector('#queue-list button').click();
queueText('Can we see the proof?');
const send = document.getElementById('send');
const sendQueue = document.getElementById('send-queue');
sendQueue.click();
await settled();
check(calls.length === 1 && calls[0][0] === 'asks' && field(1).value === original && send.disabled,
      'The message-only send included or discarded the removed custom draft');
const editor = field(1);
editor.focus();
editor.setSelectionRange(original.length, original.length);
check(document.execCommand('insertText', false, ' before printing'), 'The draft could not be edited');
const revised = 'Wait for the first proof before printing';
check(!send.disabled && !sendQueue.disabled && queueItems().length === 1 &&
      queueItems()[0].includes('Q4.1') && queueItems()[0].includes(revised),
      'Typing did not put the custom answer back in both send paths');
check(document.execCommand('undo') && editor.value === original &&
      send.disabled && sendQueue.disabled && queueItems().length === 0,
      'Native Undo restored the removed answer to the send queue');
queueText('Please include the cover.');
sendQueue.click();
await settled();
check(calls.length === 2 && calls[1][0] === 'asks' && field(1).value === original && send.disabled,
      'The follow-up sent the removed answer after native Undo');
editor.focus();
check(document.execCommand('redo') && editor.value === revised && !send.disabled && !sendQueue.disabled,
      'Native Redo did not requeue the revised answer');
sendQueue.click();
await settled();
check(calls.length === 3 && calls[2][0] === 'answers' &&
      calls[2][1].part === 'Q4.1' && calls[2][1].answer === revised,
      'The send did not use the edited custom answer');
""")

    def test_an_option_reply_waiting_for_the_store_lock_cannot_split_an_answer_send(self) -> None:
        home = self.inbox()
        port = self.start("/qd", home=home, ask_home=home)
        endpoint = "/qd/answers"
        answer = {"part": "Q4.1", "answer": "Hold", "revision": ""}
        status, original = self.post(port, endpoint, answer)
        self.assertEqual((status, original["delivery"]), (200, "sent"))
        whole = self.ask_body(port, "Q4", "Can the quantity wait?")
        self.assertEqual(self.post(port, "/qd/asks", whole)[0], 200)

        lock = quarterdeck.locked_store
        replies, attempts = [], []

        @contextmanager
        def reply_at_unlock(path):
            with lock(path):
                yield
            if not replies:
                with patch.object(quarterdeck, "locked_store", lock):
                    replies.append(self.run_cli("reply", "Q4", "--ask", whole["id"], "--text", "New cover option.",
                                                "--clarify", "Q4.1=Wait for a proof.", "--option-changed", "Q4.1")[0])

        def accept_saved_note(_home, request_id, text):
            saved = self.store()["items"][3]["answers"]["1"]
            attempts.append((request_id, text, saved["request_id"], saved["delivery"], replies.copy()))
            return True

        with patch.object(quarterdeck, "locked_store", reply_at_unlock), patch.object(
            quarterdeck, "deliver_note", accept_saved_note
        ):
            status, result = self.post(port, endpoint, {**answer, "answer": "Merge", "revision": original["revision"]})

        self.assertEqual((status, result["answer"], result["delivery"]), (200, "Merge", "sent"))
        self.assertEqual(replies, [0])
        saved = self.store()["items"][3]["answers"]["1"]
        self.assertEqual(attempts, [(saved["request_id"], "Q4.1 Merge", saved["request_id"], "pending", [])])
        self.assertEqual(self.post(port, endpoint, {**answer, "revision": saved["revision"]})[0], 409)
        self.assertEqual(self.store()["items"][3]["answers"]["1"], saved)

    def test_a_changed_option_holds_its_draft_for_review_and_the_card_shows_what_was_clarified(self) -> None:
        home = self.inbox(accept=False)
        port = self.start("/qd", home=home, ask_home=home)
        page_before_reply = self.fetch(port, "/qd/q4.html")[1]
        answer = {"part": "Q4.1", "answer": "Hold", "revision": ""}
        original_at = "2026-11-24T01:00:00Z"
        with patch.object(quarterdeck, "now_text", return_value=original_at):
            status, pending = self.post(port, "/qd/answers", answer)
        self.assertEqual((status, pending["delivery"]), (200, "pending"))
        legacy = self.store()
        del legacy["items"][3]["answers"]["1"]["answer_marks"]
        quarterdeck.store_path().write_text(json.dumps(legacy))
        whole = self.ask_body(port, "Q4", "Can the quantity wait?")
        status, saved_ask = self.post(port, "/qd/asks", whole)
        self.assertEqual((status, saved_ask["delivery"]), (200, "pending"))
        with patch.object(quarterdeck, "now_text", return_value="2026-11-25T01:00:00.000001Z"):
            code, _, error = self.run_cli("reply", "Q4", "--ask", whole["id"], "--text", "Yes, a proof first.",
                                          "--clarify", "Q4.1=This choice is for the cover proof.",
                                          "--clarify", "Q4.2=The printer can make one sample first.", "--option-changed", "q4.1")
        self.assertEqual((code, error), (0, ""))
        reply = self.messages("item", 3)[1]
        self.assertEqual(reply["clarify"], {"1": {"text": "This choice is for the cover proof.", "option_changed": True},
                                            "2": {"text": "The printer can make one sample first.", "option_changed": False}})
        page = self.fetch(port, "/qd/q4.html")[1]
        first, second = page.split('id="Q4.2"', 1)
        self.assertIn("Clarified after discussion", first)
        self.assertIn("This choice is for the cover proof.", first)
        self.assertIn("An option changed.", first)
        self.assertIn("The printer can make one sample first.", second)
        self.assertNotIn("An option changed.", second)
        retries_before = (home / "attempts").read_text()
        for stale in (answer, {**answer, "revision": pending["revision"]},
                      {**answer, "answer": "Merge", "revision": pending["revision"]}):
            status, conflict = self.post(port, "/qd/answers", stale)
            self.assertEqual(status, 409)
            self.assertIn("This choice is for the cover proof.", conflict["clarification"])
        self.assertEqual((home / "attempts").read_text(), retries_before)
        reaffirmed_answer = {**answer, "option_revision": reply["id"]}
        accepted_at = "2026-11-25T01:00:00.000002Z"
        with patch.object(quarterdeck, "now_text", return_value=accepted_at):
            status, reaffirmed = self.post(port, "/qd/answers", reaffirmed_answer)
        self.assertEqual((status, reaffirmed["delivery"], reaffirmed["revision"]), (200, "pending", pending["revision"]))
        self.assertEqual(reaffirmed["answered_at"], accepted_at)
        self.assertEqual(self.store()["items"][3]["answers"]["1"]["answer_marks"],
                         [{"via": "desk", "answered_at": original_at}])
        code, report, error = self.run_cli("stats", "--json", "--since", "2026-11-25")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(report)["total"]["answered_desk"], 0)
        page_after_acceptance = self.fetch(port, "/qd/q4.html")[1]
        self.inbox()
        status, confirmed = self.post(port, "/qd/answers", reaffirmed_answer)
        self.assertEqual((status, confirmed["delivery"]), (200, "sent"))
        self.assertEqual(confirmed["revision"], pending["revision"])
        self.assertEqual((home / "notes").read_text(), "Q4.1 Hold\n")
        snapshot = json.loads(self.fetch(port, "/qd/q4.discussion.json")[1])
        self.check_page(port, r"""
const requests = [];
window.fetch = async (url, options) => {
  if (url === 'q4.discussion.json') return {ok: true, json: async () => snapshot};
  const body = JSON.parse(options.body);
  requests.push([url, body]);
  if (url === 'asks') return {ok: true, status: 200, json: async () => ({
    delivery: 'sent', message: 'Ask sent.',
    thread: `<ol><li data-ask-id="${body.id}"></li></ol>`,
  })};
  return {ok: true, status: 200, json: async () => confirmed};
};
const send = document.getElementById('send');
const sendQueue = document.getElementById('send-queue');
const opener = document.getElementById('open-item-ask');
opener.click();
type(1, 'Hold');
[...document.querySelectorAll('#queue-list button')].find(b => b.textContent === 'Remove from send').click();
const composer = document.getElementById('ask-Q4');
composer.value = 'Can the quantity wait?';
composer.dispatchEvent(new Event('input', {bubbles: true}));
document.getElementById('queue-add').click();
sendQueue.click();
await settled();
check(requests.length === 1 && requests[0][0] === 'asks' && requests[0][1].part === 'Q4',
      'The request sent an answer or went to the wrong item');
type(2, 'Red');
const remove = document.querySelector('#queue-list button');
remove.focus();
await refreshDiscussion();
await refreshDiscussion();
const discussion = document.getElementById('Q4');
check(discussion.querySelector('.thread').open &&
      discussion.querySelector('[data-reply-id] .record').textContent === 'Yes, a proof first.' &&
      discussion.querySelector('.badge').textContent === 'Replied',
      'The owner could not read the set reply in the open thread before following up');
check(document.activeElement === remove, 'The poll replaced a focused queue control');
type(2, '');
check(field(1).value === 'Hold' && !card(1).querySelector('.review').hidden &&
      card(2).querySelector('.clarified').textContent.includes('The printer can make one sample first.'),
      'The open page lost the draft or missed the clarification');
type(1, ' Hold ');
send.click();
sendQueue.click();
check(requests.length === 1 && send.disabled && sendQueue.disabled && !card(1).querySelector('.review').hidden,
      'A whitespace-only edit cleared the review hold or let a send button accept a stale draft');
card(1).querySelector('[data-choice="Merge"]').click();
document.getElementById('undo').click();
check(field(1).value === ' Hold ' && !card(1).querySelector('.review').hidden && send.disabled,
      'Undo released the review-held draft');
card(1).querySelector('.keep-draft').click();
check(send.disabled && sendQueue.disabled, 'Confirming the draft cleared its send exclusion');
composer.value = 'Please include the cover in the proof.';
composer.dispatchEvent(new Event('input', {bubbles: true}));
document.getElementById('queue-add').click();
sendQueue.click();
await settled();
check(requests.length === 2 && requests[1][0] === 'asks' && field(1).value === ' Hold ' && send.disabled,
      'Following up sent or discarded the cover draft');
card(1).querySelector('[data-choice="Hold"]').click();
sendQueue.click();
await settled();
check(requests.length === 3 && requests[2][1].option_revision === revision &&
      requests[2][1].answer === 'Hold' && card(2).dataset.state === 'open',
      'The confirmed answer did not carry the current option revision');
""", page=page_before_reply, setup=f"""
const snapshot = {json.dumps(snapshot)};
const revision = {json.dumps(reply["id"])};
const confirmed = {json.dumps(confirmed)};
let refreshDiscussion;
window.setInterval = callback => {{ refreshDiscussion = callback; }};
""")
        self.check_page(port, r"""
window.fetch = async () => ({ok: true, json: async () => snapshot});
document.getElementById('open-item-ask').click();
type(1, 'Hold');
card(1).querySelector('[data-choice="Merge"]').click();
type(2, 'Red');
const [coverRemove, quantityRemove] = document.querySelectorAll('#queue-list button');
coverRemove.focus();
await refreshDiscussion();
check(!coverRemove.isConnected && document.activeElement === quantityRemove && field(1).value === 'Merge',
      'When review held the cover draft, focus did not move to the remaining queue control');
document.getElementById('undo').click();
check(field(1).value === 'Hold' && !card(1).querySelector('.review').hidden &&
      document.querySelectorAll('#queue-list li').length === 1,
      'Undo across an option change released a previous draft');
type(1, '');
document.getElementById('suggest').click();
check(field(1).value === 'Merge' && !card(1).querySelector('.review').hidden &&
      document.querySelectorAll('#queue-list li').length === 1,
      'A bulk suggestion released the original draft without confirmation');
document.getElementById('undo').click();
type(1, 'Merge');
check(!card(1).querySelector('.review').hidden && document.querySelectorAll('#queue-list li').length === 1,
      'Restoring a draft after Undo released the review hold');
""", page=page_before_reply, setup=f"""
const snapshot = {json.dumps(snapshot)};
let refreshDiscussion;
window.setInterval = callback => {{ refreshDiscussion = callback; }};
""")
        self.check_page(port, self.SET_STUB + r"""
field(1).value = 'Hold';
type(2, 'Red');
check(document.getElementById('send').textContent === 'Send 1 answer' && queueItems().length === 1 &&
      queueItems()[0].includes('Q4.2') && field(1).value === 'Hold' && !card(1).querySelector('.review').hidden,
      'A draft on a changed option was not held for review');
card(1).querySelector('.keep-draft').click();
check(document.getElementById('send').textContent === 'Send 2 answers' && card(1).querySelector('.review').hidden &&
      queueItems().length === 2, 'Keeping the draft did not release it');
""", page=page)
        self.check_page(port, r"""
document.getElementById('open-item-ask').click();
check(card(1).querySelector('.review').hidden && field(1).value === 'Hold' &&
      !document.getElementById('send').disabled && !document.getElementById('send-queue').disabled,
      'Reload put a confirmed pending answer back on hold');
""", page=page_after_acceptance)

    def test_live_option_review_reveals_a_folded_correction(self) -> None:
        home = self.inbox()
        port = self.start("/qd", home=home, ask_home=home)
        status, saved = self.post(port, "/qd/answers", {"part": "Q4.1", "answer": "Merge", "revision": ""})
        self.assertEqual((status, saved["delivery"]), (200, "sent"))
        before_reply = self.fetch(port, "/qd/q4.html")[1]
        ask = self.ask_body(port, "Q4", "Can we see the proof?")
        self.assertEqual(self.post(port, "/qd/asks", ask)[0], 200)
        self.assertEqual(self.run_cli("reply", "Q4", "--ask", ask["id"], "--text", "Check the revised proof.",
                                      "--clarify", "Q4.1=The cover option changed.", "--option-changed", "Q4.1")[0], 0)
        snapshot = json.loads(self.fetch(port, "/qd/q4.discussion.json")[1])
        self.assertEqual(self.run_cli("reply", "Q4", "--ask", ask["id"], "--text", "Use the new proof.",
                                      "--clarify", "Q4.1=The printer revised it again.", "--option-changed", "Q4.1")[0], 0)
        newer_snapshot = json.loads(self.fetch(port, "/qd/q4.discussion.json")[1])
        for via_poll in (True, False):
            with self.subTest(via_poll=via_poll):
                self.check_page(port, r"""
const calls = [];
let discussion = snapshot;
window.fetch = async (url, options) => {
  calls.push(url);
  const result = url === 'q4.discussion.json' || !viaPoll ? discussion : {thread: ''};
  return {ok: true, status: 200, json: async () => ({...result, delivery: 'sent', message: 'Ask sent.'})};
};
const fold = card(1).closest('details.done');
fold.open = true;
type(1, 'Hold');
fold.open = false;
document.getElementById('open-item-ask').click();
document.querySelector('#queue-list button').click();
const composer = document.getElementById('ask-Q4');
composer.value = 'Can we see the proof?';
composer.dispatchEvent(new Event('input', {bubbles: true}));
document.getElementById('queue-add').click();
document.getElementById('send-queue').click();
await settled();
if (viaPoll) await refreshDiscussion();
check(calls.join() === (viaPoll ? 'asks,q4.discussion.json' : 'asks'), 'The excluded correction was sent');
const review = card(1).querySelector('.review');
check(fold.open && review.getClientRects().length > 0 && field(1).value === 'Hold' &&
      document.getElementById('send').disabled && document.getElementById('send-queue').disabled,
      'The correction stayed folded, lost its text, or entered a send without review');
const editor = field(1);
editor.focus();
editor.setSelectionRange(editor.value.length, editor.value.length);
check(document.execCommand('insertText', false, '.') && editor.value === 'Hold.' &&
      !review.hidden && document.getElementById('send').disabled,
      'A changed draft bypassed explicit confirmation');
check(document.execCommand('undo') && editor.value === 'Hold' && document.getElementById('send').disabled &&
      document.execCommand('redo') && editor.value === 'Hold.' && document.getElementById('send').disabled,
      'Native Undo or Redo released the review hold');
check(document.execCommand('delete') && editor.value === 'Hold' && !review.hidden &&
      document.getElementById('send').disabled && document.getElementById('send-queue').disabled,
      'Typing and deleting a character released the original draft without confirmation');
type(1, 'Wait for the proof.');
check(!review.hidden && document.getElementById('send').disabled && document.getElementById('send-queue').disabled,
      'Changing the answer text released the review hold');
card(1).querySelector('[data-choice="Hold"]').click();
check(!review.hidden && document.getElementById('send').disabled && document.getElementById('send-queue').disabled,
      'Selecting an option released the review hold');
card(1).querySelector('.keep-draft').click();
check(review.hidden && !document.getElementById('send').disabled && !document.getElementById('send-queue').disabled,
      'Explicit confirmation did not release the held draft');
discussion = newerSnapshot;
await refreshDiscussion();
check(!card(1).querySelector('.review').hidden && field(1).value === 'Hold' &&
      document.getElementById('send').disabled && document.getElementById('send-queue').disabled,
      'A later option change did not require a fresh confirmation');
""", page=before_reply, setup=f"""
const snapshot = {json.dumps(snapshot)};
const newerSnapshot = {json.dumps(newer_snapshot)};
const viaPoll = {json.dumps(via_poll)};
let refreshDiscussion;
window.setInterval = callback => {{ refreshDiscussion = callback; }};
""")

    def test_mixed_queue_reports_delivered_pending_and_rejected_entries(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.check_page(port, self.SET_STUB + r"""
const sent = window.fetch;
let refusal = 403;
const quantityAttempts = [];
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  if (body.part === 'Q4.2') {
    quantityAttempts.push(body);
    if (refusal) return {ok: false, status: refusal, text: async () => 'Request refused.'};
  }
  const response = await sent(url, options);
  if (body.part === 'Q4.1') {
    return {...response, json: async () => ({...(await response.json()), delivery: 'pending'})};
  }
  return response;
};
type(1, 'Hold');
type(2, 'Red');
queueText('Can we see the proof first?');
document.getElementById('send-queue').click();
await settled();
check(status().textContent.startsWith('1 sent. 1 saved for retry. 1 failed.') &&
      card(1).querySelector('.delivery').textContent === 'Not delivered' && field(2).value === 'Red',
      'The successful set message hid an undelivered or rejected answer');
document.querySelector('#queue-list li button').click();
const send = document.getElementById('send');
const sendQueue = document.getElementById('send-queue');
for (const [code, button] of [[405, send], [415, sendQueue], [0, send]]) {
  check(!send.disabled && !sendQueue.disabled && field(2).value === 'Red',
        'A request-level refusal blocked the unchanged draft');
  refusal = code;
  button.click();
  await settled();
  if (code) check(status().textContent.includes('1 answer rejected.') && status().textContent.includes('Request refused.'),
                  'The retry did not report the definite refusal');
}
check(quantityAttempts.length === 4 && quantityAttempts.every(body => JSON.stringify(body) === JSON.stringify(quantityAttempts[0])) &&
      card(2).querySelector('.saved-answer .record').textContent === 'Red',
      'The corrected endpoint could not accept the preserved draft without another edit');
""")

    def test_mixed_queue_keeps_lost_answer_and_message_responses_unconfirmed(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.check_page(port, self.SET_STUB + r"""
window.fetch = async (url, {body}) => {
  calls.push([url, JSON.parse(body)]);
  throw new Error('Connection lost');
};
type(1, 'Hold');
type(2, 'Red');
queueText('Can we see the proof first?');
document.getElementById('send-queue').click();
await settled();
check(status().textContent.startsWith('0 sent. 0 saved for retry. 0 failed. 2 unconfirmed. 1 skipped.'),
      'A lost response was reported as a rejection or a confirmed save');
check(calls.map(([, body]) => body.part).join() === 'Q4.1,Q4' && field(1).value === 'Hold' && field(2).value === 'Red',
      'The interrupted request lost drafts or attempted the skipped answer');
document.querySelector('#Q4 .pending-asks button').click();
await settled();
check(calls[2][1].id === calls[1][1].id && calls[2][1].action === 'retry',
      'The unconfirmed set message lost its original retry ID');
""")

    def test_clarifications_belong_to_a_reply_to_the_whole_set(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        whole = self.ask_body(port, "Q4", "Can the quantity wait?")
        part = self.ask_body(port, "Q4.1", "Why?")
        for body in (whole, part):
            self.assertEqual(self.post(port, "/qd/asks", body)[0], 200)
        starter = {"id": uuid.uuid4().hex, "title": "Lamp colours", "text": "Can we see the amber?",
                   "home": whole["home"]}
        self.assertEqual(self.post(port, "/qd/conversations", starter)[0], 200)
        before = self.store()
        for target, ask, extra in (("Q4.1", part["id"], ["--clarify", "Q4.1=Context."]),
                                   ("Q4", whole["id"], ["--clarify", "Q4.3=Context."]),
                                   ("Q4", whole["id"], ["--clarify", "Q4.1"]),
                                   ("Q4", whole["id"], ["--clarify", "Q1.1=Context."]),
                                   ("Q4", whole["id"], ["--clarify", "Q4.1=A.", "--clarify", "Q4.1=B."]),
                                   ("Q4", whole["id"], ["--option-changed", "Q4.1"])):
            with self.subTest(target=target, extra=extra):
                self.assertEqual(self.run_cli("reply", target, "--ask", ask, "--text", "Reply.", *extra)[0], 2)
                self.assertEqual(self.store(), before)
        standalone_reply = ("reply", "Q5", "--ask", starter["id"], "--text", "Reply.")
        code, _, error = self.run_cli(*standalone_reply, "--clarify", "Q5.1=Context.")
        self.assertEqual(code, 2)
        self.assertIn("--clarify needs a question item", error)
        self.assertEqual(self.store(), before)
        self.assertEqual(self.run_cli(*standalone_reply)[0], 0)
        self.assertEqual(self.run_cli("reply", "Q4", "--ask", whole["id"], "--text", "Fine.", "--clarify", "Q4.1=Context.")[0], 0)
        damaged = self.store()
        damaged["items"][3]["conversation"]["item"][1]["clarify"]["9"] = {"text": "x", "option_changed": False}
        quarterdeck.store_path().write_text(json.dumps(damaged))
        self.assertEqual(self.fetch(port, "/qd/q4.html")[0], 500)

    def test_the_set_discussion_stays_available_from_answered_with_each_question_traceable(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        whole = self.ask_body(port, "Q4", "Can the quantity wait?")
        self.assertEqual(self.post(port, "/qd/asks", whole)[0], 200)
        self.assertEqual(self.run_cli("reply", "Q4", "--ask", whole["id"], "--text", "Yes.")[0], 0)
        self.assertEqual(self.run_cli("mark", "Q4", "answered", "--via", "elsewhere")[0], 0)
        index = self.fetch(port, "/qd/")[1]
        answered = index.split('id="panel-answered"', 1)[1].split("</section>", 1)[0]
        self.assertEqual(re.findall(r'<a [^>]*href="(q4\.html[^"]*)"[^>]*>([^<]*)</a>', answered),
                         [("q4.html", "Two decisions")])
        page = self.fetch(port, "/qd/q4.html")[1]
        self.assertIn("Discuss this set", page)
        self.assertIn("Can the quantity wait?", page)
        self.assertIn('<span class="id">Q4.1</span>', page)
        self.assertIn("Answered elsewhere", page)

    def test_a_closed_set_discussion_refreshes_on_an_open_page(self) -> None:
        port = self.start("/qd", ask_home=self.inbox(accept=False))
        status, result = self.ask(port, "Q4", "Can we see the proof?")
        self.assertEqual((status, result["delivery"], result["clarifications"]), (200, "pending", {}))
        self.assertEqual(self.run_cli("mark", "Q4", "closed")[0], 0)
        status, before_reply, headers = self.fetch(port, "/qd/q4.html")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Security-Policy"), "frame-ancestors 'none'")
        self.assertEqual(self.run_cli("mark", "Q4.2", "open")[0], 0)
        ask, = self.messages("item", 3)
        self.assertEqual(self.run_cli("reply", "Q4", "--ask", ask["id"], "--text", "Here is the proof.",
                                      "--clarify", "Q4.1=The cover proof is ready.")[0], 0)
        status, body, _ = self.fetch(port, "/qd/q4.discussion.json")
        self.assertEqual(status, 200)
        snapshot = json.loads(body)
        self.assertEqual(set(snapshot["clarifications"]), {"Q4.1"})
        self.assertIn("data-retry-ask=", snapshot["thread"])
        status, follow_up = self.ask(port, "Q4", "Can we see the revised proof?")
        self.assertEqual(status, 200)
        self.assertEqual(follow_up["clarifications"], snapshot["clarifications"])
        self.check_page(port, r"""
const discussion = document.getElementById('Q4');
const summary = discussion.querySelector('summary');
summary.click();
check(!document.getElementById('send') && !discussion.querySelector('[data-reply-id]'),
      'The closed discussion unexpectedly had answer controls or an initial reply');
window.fetch = async url => {
  check(url === 'q4.discussion.json', 'The closed discussion refreshed another item');
  return {ok: true, json: async () => snapshot};
};
await refreshDiscussion();
check(summary.parentElement.open && discussion.querySelector('[data-reply-id] .record').textContent === 'Here is the proof.' &&
      discussion.querySelector('.badge').textContent === 'Replied' &&
      card(1).querySelector('.clarified .record').textContent === 'The cover proof is ready.',
      'The open read-only discussion did not display the reply and clarification');
check(discussion.querySelector('[data-ask-id] .delivery').textContent === 'Not delivered' &&
      !discussion.querySelector('[data-retry-ask]'),
      'The read-only tab showed a retry control it could not use');
""", page=before_reply, setup=f"""
const snapshot = {json.dumps(snapshot)};
let refreshDiscussion;
window.setInterval = callback => {{ refreshDiscussion = callback; }};
""")

    def test_a_correction_keeps_the_previous_answer_traceable_on_the_card(self) -> None:
        home = self.inbox()
        port = self.start("/qd", home=home, ask_home=home)
        endpoint = "/qd/answers"
        original = {"part": "Q4.1", "answer": "Hold <for proof>", "revision": ""}
        original_at = "2026-11-23T01:00:00Z"
        with patch.object(quarterdeck, "now_text", return_value=original_at):
            status, first = self.post(port, endpoint, original)
        self.assertEqual((status, first["delivery"]), (200, "sent"))
        saved = self.store()["items"][3]["answers"]["1"]
        old_entry = {"answer": original["answer"], "answered_at": original_at, "request_id": saved["request_id"]}
        status, posted = self.ask(port, "Q4", "Can we see the proof?")
        self.assertEqual((status, posted["delivery"]), (200, "sent"))
        ask, = self.messages("item", 3)
        with patch.object(quarterdeck, "now_text", return_value="2026-11-24T01:00:00.000001Z"):
            self.assertEqual(self.run_cli("reply", "Q4", "--ask", ask["id"], "--text", "The proof is ready.",
                                          "--clarify", "Q4.1=The cover changed.", "--option-changed", "Q4.1")[0], 0)
        option_revision = self.messages("item", 3)[1]["id"]
        live_page = self.fetch(port, "/qd/q4.html")[1]
        reaffirmation = {**original, "revision": first["revision"], "option_revision": option_revision}
        accepted_at = "2026-11-24T01:00:00.000002Z"
        with patch.object(quarterdeck, "now_text", return_value=accepted_at):
            status, reaffirmed = self.post(port, endpoint, reaffirmation)
        self.assertEqual((status, reaffirmed["answered_at"], reaffirmed["revision"]),
                         (200, accepted_at, first["revision"]))
        correction = {**reaffirmation, "answer": "Merge"}
        with patch.object(quarterdeck, "now_text", return_value="2026-11-25T01:00:00.000001Z"):
            status, corrected = self.post(port, endpoint, correction)
        self.assertEqual((status, corrected["delivery"]), (200, "sent"))
        self.assertEqual(self.post(port, endpoint, correction), (200, corrected))
        for action in ("open", "closed", "open"):
            self.assertEqual(self.run_cli("mark", "Q4.1", action)[0], 0)
        self.assertEqual(self.run_cli("mark", "Q4", "answered", "--via", "elsewhere")[0], 0)
        self.assertEqual(self.store()["items"][3]["answers"]["1"]["earlier_answers"], [old_entry])
        self.assertNotIn(old_entry["request_id"], corrected["earlier_answers_html"])
        self.assertIn('<a class="lib-title" href="q4.html">Two decisions</a>', self.fetch(port, "/qd/")[1])

        check_history = r"""
const outer = card(1).closest('details.done');
if (outer) outer.open = true;
const history = [...card(1).querySelectorAll('details')].find(box => box.querySelector('summary').textContent === 'Earlier answers');
check(history && !history.open && card(1).querySelector('.saved-answer .record').textContent === 'Merge',
      'The card did not show the correction with a collapsed history');
history.querySelector('summary').click();
const entries = history.querySelectorAll('li');
check(history.open && entries.length === 1 && entries[0].querySelector('.record').textContent === predecessor.answer &&
      entries[0].querySelector('time').dateTime === predecessor.answered_at &&
      !entries[0].textContent.includes(predecessor.request_id),
      'Earlier answers did not show just the previous text and time');
"""
        setup = f"const predecessor = {json.dumps(old_entry)};"
        self.check_page(port, r"""
window.fetch = async (url, options) => {
  check(url === 'answers' && JSON.parse(options.body).part === 'Q4.1', 'The correction targeted another question');
  return {ok: true, status: 200, json: async () => corrected};
};
card(1).closest('details.done').open = true;
type(1, 'Merge');
card(1).querySelector('.keep-draft').click();
document.getElementById('send').click();
await settled();
""" + check_history, page=live_page, setup=setup + f"const corrected = {json.dumps(corrected)};")
        self.check_page(port, check_history, setup=setup)
        export = self.root / "export"
        self.assertEqual(self.run_cli("render", "--output", str(export / "index.html"))[0], 0)
        exported = (export / "q4.html").read_text()
        self.assertIn('<p class="record">Merge</p>', exported)
        self.assertNotIn(escape(original["answer"]), exported)
        self.assertNotIn(old_entry["request_id"], exported)

    def test_reply_images_fit_both_threads_on_a_phone(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        png = tiny_png((40, 160, 40), 1200)
        image = self.picture("mockup.png", png)
        for target in ("Q4", "Q4.1"):
            ask = self.ask_body(port, target, "Show the layout.")
            self.assertEqual(self.post(port, "/qd/asks", ask)[0], 200)
            self.assertEqual(self.run_cli("reply", target, "--ask", ask["id"], "--text", "Here it is.",
                                          "--image", str(image), "--alt", "Layout mockup")[0], 0)
        initial_page = self.fetch(port, "/qd/q4.html")[1]
        status, replacement = self.ask(port, "Q4", "Show another layout.")
        self.assertEqual(status, 200)
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

        def embed_picture(html):
            return re.sub(r'(?<=src=")reply-images/q4/[^"]+', url, html).replace('loading="lazy"', 'loading="eager"')

        replacement["thread"] = embed_picture(replacement["thread"])
        page = embed_picture(initial_page)
        self.check_page(port, r"""
check(matchMedia('(width: 390px)').matches, 'Expected a 390px browser viewport, got ' + innerWidth);
const whole = document.getElementById('Q4');
async function checkImage(section) {
  section.querySelector('details').open = true;
  const image = section.querySelector('.thread img');
  if (!image.complete) {
    await new Promise((resolve, reject) => {
      image.addEventListener('load', resolve, {once: true});
      image.addEventListener('error', reject, {once: true});
    });
  }
  const imageBox = image.getBoundingClientRect();
  const cardBox = section.getBoundingClientRect();
  check(image.naturalWidth === 1200 && imageBox.width > 0 && imageBox.right <= cardBox.right,
        'The reply image overflowed ' + section.id);
}
await checkImage(whole);
await checkImage(card(1));
window.fetch = async () => ({ok: true, status: 200, json: async () => replacement});
document.getElementById('open-item-ask').click();
const editor = whole.querySelector('textarea');
editor.value = 'Show another layout.';
editor.dispatchEvent(new Event('input', {bubbles: true}));
document.getElementById('queue-add').click();
document.getElementById('send-queue').click();
await settled();
check(whole.querySelector('.thread-messages').textContent.includes('Show another layout.'),
      'The new message did not appear in the set thread');
await checkImage(whole);
""", setup=f"const replacement = {json.dumps(replacement)};", page=page, viewport_width=390)

    def test_confirmed_ask_conflicts_restore_local_drafts_and_keep_saved_asks(self) -> None:
        port = self.start("/qd", ask_home=self.inbox(accept=False))
        self.assertEqual(self.ask(port, "Q4.2", "A saved preview request.")[0], 200)
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                self.check_page(port, r"""
const calls = [];
const conflict = 'Firstmate home changed. This ask belongs to the original home.';
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  calls.push([url, body]);
  if (interrupted && body.text && !body.action) throw new Error('Connection lost');
  return {status: 409, json: async () => ({message: conflict, saved: !body.text || body.text.startsWith("Saved ")})};
};
const whole = document.getElementById('Q4');
const sendAsk = section => {
  if (section !== whole) return section.querySelector('.ask-button').click();
  document.getElementById('queue-add').click();
  document.getElementById('send-queue').click();
};
document.getElementById('open-item-ask').click();
for (const section of [whole, card(1)]) {
  const editor = section.querySelector('textarea');
  const draft = 'Show the new preview for ' + section.id;
  editor.value = draft;
  editor.dispatchEvent(new Event('input', {bubbles: true}));
  const start = calls.length;
  sendAsk(section);
  await settled();
  if (interrupted) {
    section.querySelector('.pending-asks button').click();
    await settled();
    const first = calls[start][1];
    const retry = calls[start + 1][1];
    check(first.id === retry.id && first.home === retry.home, 'Retry changed the ask identity or home');
  }
  check(calls.length === start + (interrupted ? 2 : 1), 'Conflict automatically retried the ask');
  check(editor.value === draft && editor.getAttribute('aria-invalid') === 'true' &&
        (section !== whole || document.getElementById('send-queue').disabled),
        'The confirmed conflict did not return the draft and its error');
  check(!section.querySelector('[data-local-ask]'), 'A rejected draft stayed pending');
  check(document.getElementById('send').disabled, 'A conflicted ask entered the answer batch');
  editor.value = 'Saved ' + draft;
  editor.dispatchEvent(new Event('input', {bubbles: true}));
  const first = calls.length;
  sendAsk(section);
  await settled();
  if (section === whole && !interrupted) {
    check(status().textContent === '0 sent. 1 saved for retry. 0 failed. ' + conflict,
          'The saved conflict did not count as pending with the reason delivery was blocked');
  }
  const recovery = section.querySelector('[data-local-ask] button');
  recovery.click();
  await settled();
  check(editor.value === '' && recovery.isConnected && document.getElementById('send').disabled,
        'A saved conflict restored an ask as a fresh draft');
  check(recovery.closest('[data-local-ask]').querySelector('.delivery').textContent === 'Pending',
        'A conflict changed the pending delivery label');
  check(calls[first][1].id === calls.at(-1)[1].id && calls[first][1].home === calls.at(-1)[1].home,
        'A saved conflict lost the ask identity or home');
}
const savedRetry = card(2).querySelector('[data-retry-ask]');
savedRetry.click();
await settled();
check(calls.at(-1)[1].action === 'retry' && !('text' in calls.at(-1)[1]) &&
      savedRetry.isConnected && field(2).value === '',
      'A saved ask conflict changed the thread or copied the ask into the editor');
""", setup=f"const interrupted = {json.dumps(interrupted)};")

    def test_forbidden_ask_restores_new_drafts_but_keeps_interrupted_ids(self) -> None:
        port = self.start("/qd", ask_home=self.inbox(accept=False))
        without_home = self.start("/qd")
        self.assertEqual(self.ask(port, "Q4.2", "Show the saved preview.")[0], 200)
        draft = self.ask_body(port, "Q4", "Show the layout.")
        self.assertEqual(self.post(without_home, "/qd/asks", draft),
                         (403, "Configure an available firstmate_home to ask Firstmate."))
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                self.check_page(port, r"""
const calls = [];
const refusal = 'Configure an available firstmate_home to ask Firstmate.';
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  calls.push(body);
  if (interrupted && !body.action) throw new Error('Connection lost');
  return {status: 403, ok: false, text: async () => refusal};
};
const whole = document.getElementById('Q4');
const sendAsk = section => {
  if (section !== whole) return section.querySelector('.ask-button').click();
  document.getElementById('queue-add').click();
  document.getElementById('send-queue').click();
};
document.getElementById('open-item-ask').click();
for (const section of [whole, card(1)]) {
  const editor = section.querySelector('textarea');
  const draft = '  Show the layout.  ';
  editor.value = draft;
  editor.dispatchEvent(new Event('input', {bubbles: true}));
  sendAsk(section);
  await settled();
  if (interrupted) {
    const first = calls.at(-1);
    const retry = section.querySelector('[data-local-ask] button');
    retry.click();
    await settled();
    const second = calls.at(-1);
    check(second.action === 'retry' && second.id === first.id && second.home === first.home,
          'The refused retry lost its original identity');
    check(retry.isConnected && editor.value === '', 'The refused retry discarded the pending ask');
  } else {
    check(editor.value === (section === whole ? draft.trim() : draft) &&
          !editor.disabled && editor.getAttribute('aria-invalid') === 'true',
          'A first-attempt refusal did not restore the editable draft');
    check(!section.querySelector('[data-local-ask]'), 'A first-attempt refusal stayed pending');
  }
  check(status().textContent === (section === whole && !interrupted ?
        '0 sent. 0 saved for retry. 1 failed. ' + refusal : refusal),
        'A definite response was reported as an interruption');
  check(document.getElementById('send').disabled, 'The refused ask was queued as an answer');
}
const savedRetry = card(2).querySelector('[data-retry-ask]');
savedRetry.click();
await settled();
check(savedRetry.isConnected && calls.at(-1).id === savedRetry.dataset.retryAsk && field(2).value === '',
      'The refused saved retry changed its original entry');
""", setup=f"const interrupted = {json.dumps(interrupted)};")

    def test_restored_retry_discards_only_its_own_option_history(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.assertEqual(self.post(port, "/qd/answers", {"part": "Q4.1", "answer": "Merge", "revision": ""})[0], 200)
        self.check_page(port, r"""
const draft = 'Show the alternate layout.';
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  if (!body.action) throw new Error('Connection lost before saving');
  return {status: 409, json: async () => ({message: 'Firstmate home changed.', saved: false})};
};
type(1, draft);
card(1).querySelector('.ask-button').click();
await settled();
card(2).querySelector('[data-choice="Blue"]').click();
card(1).querySelector('[data-choice="Hold"]').click();
card(1).querySelector('[data-choice="Merge"]').click();
const retry = card(1).querySelector('[data-local-ask] button');
check(!retry.disabled, 'Returning to the saved answer did not enable retry');
retry.click();
await settled();
check(field(1).value === draft && !retry.isConnected, 'The rejected retry did not restore the draft');
const undo = document.getElementById('undo');
check(!undo.hidden, 'Restoring the draft discarded the sibling option history');
undo.click();
check(field(1).value === draft && field(2).value === '' && undo.hidden,
      'Undo overwrote the restored draft or failed to undo the sibling option');
check(!card(1).querySelector('.ask-button').disabled && document.getElementById('send').disabled,
      'Undo changed the restored draft from an ask into an answer');
""")

    def test_undo_restores_rejected_ask_without_entering_answer_batch(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                setup = f"const interrupted = {json.dumps(interrupted)};"
                self.check_page(port, r"""
const calls = [];
const draft = '  Show\x7f the preview.  ';
const corrected = 'Show the preview.';
let reject = true;
window.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  calls.push([url, body]);
  if (interrupted && calls.length === 1) throw new Error('Connection lost');
  if (url === 'asks' && reject) return {status: 400, text: async () => 'Check the question and ask.'};
  const result = url === 'answers'
    ? {state: 'answered', revision: 'new', delivery: 'sent', answer: 'Blue', answered_at: '2026-11-25T01:00:00Z'}
    : {thread: `<ol><li data-ask-id="${body.id}"><p class="record">${corrected}</p></li></ol>`, message: 'Ask sent.'};
  return {ok: true, status: 200, json: async () => result};
};
const send = document.getElementById('send');
const askButton = card(1).querySelector('.ask-button');
const pending = card(1).querySelector('.pending-asks');
type(1, draft);
askButton.click();
await settled();
if (interrupted) {
  pending.querySelector('button').click();
  await settled();
}
check(field(1).value === draft && !pending.firstElementChild, 'The failed draft was not restored to the editor');
check(field(1).getAttribute('aria-invalid') === 'true' && !askButton.disabled && send.disabled,
      'A rejected ask entered the answer batch or its error was lost');
field(1).focus();
field(1).select();
check(document.execCommand('insertText', false, corrected) && field(1).value === corrected,
      'Native replacement did not run');
check(!send.disabled && !askButton.disabled, 'A manual replacement was not eligible for both actions');
check(document.execCommand('undo') && field(1).value === draft, 'Native Undo did not restore the ask');
check(send.disabled && !askButton.disabled, 'Native Undo queued the rejected ask as an answer');
check(document.execCommand('redo') && field(1).value === corrected && !send.disabled,
      'Native Redo did not restore the replacement draft');
check(document.execCommand('undo') && field(1).value === draft, 'Second native Undo did not restore the ask');
field(1).setSelectionRange(draft.length, draft.length);
check(document.execCommand('insertText', false, ' ') && field(1).value === draft + ' ' && send.disabled,
      'Adding trailing whitespace queued the rejected ask as an answer');
check(document.execCommand('undo') && field(1).value === draft && send.disabled,
      'Undo of a whitespace edit queued the rejected ask');
check(document.execCommand('redo') && field(1).value === draft + ' ' && send.disabled,
      'Redo of a whitespace edit queued the rejected ask');
card(1).querySelector('[data-choice="Hold"]').click();
check(send.textContent === 'Send 1 answer' && askButton.disabled, 'Choosing an answer did not replace the ask draft');
document.getElementById('undo').click();
check(field(1).value === draft + ' ' && send.disabled && !askButton.disabled, 'Undo changes lost the ask-only draft');
type(2, 'Blue');
check(send.textContent === 'Send 1 answer', 'Restoring a rejected ask queued it as an answer');
send.click();
await settled();
check(calls.filter(([url]) => url === 'answers').map(([, body]) => body.part).join() === 'Q4.2',
      'Sending a sibling answer also submitted the rejected ask');
reject = false;
type(1, corrected);
askButton.click();
await settled();
const body = calls.at(-1)[1];
check(calls.at(-1)[0] === 'asks' && body.text === corrected && body.id !== calls[0][1].id,
      'The corrected ask reused the rejected request');
check(field(1).value === '' && !pending.firstElementChild && send.disabled, 'The corrected ask remained a draft');
""", setup=setup)

    def test_options_and_suggestions_fill_answers_until_edited_by_hand(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.check_page(port, r"""
const ask = n => card(n).querySelector('.ask-button');
const send = document.getElementById('send');
const undo = document.getElementById('undo');
field(2).focus();
check(document.execCommand('insertText', false, 'Sibling draft'), 'Sibling edit failed');
document.getElementById('suggest').click();
check(field(1).value === 'Merge' && ask(1).disabled, 'A bulk suggestion became an ask');
check(send.textContent === 'Send 2 answers', 'A bulk suggestion was not queued alongside the sibling draft');
undo.click();
check(field(1).value === '' && ask(1).disabled && send.textContent === 'Send 1 answer',
      'Undo changes lost the sibling draft or left the suggestion queued');
card(1).querySelector('[data-choice="Hold"]').click();
check(field(1).value === 'Hold' && ask(1).disabled, 'Selecting an option enabled Ask Firstmate');
field(1).focus();
field(1).setSelectionRange(4, 4);
check(document.execCommand('insertText', false, '?') && field(1).value === 'Hold?', 'Hand editing failed');
check(!ask(1).disabled, 'Hand editing did not enable Ask Firstmate');
check(document.execCommand('undo') && field(1).value === 'Hold' && ask(1).disabled,
      'Native Undo made the selected answer askable');
check(document.execCommand('redo') && field(1).value === 'Hold?' && !ask(1).disabled,
      'Native Redo lost the hand edit');
field(1).setSelectionRange(4, 5);
check(document.execCommand('delete') && field(1).value === 'Hold' && !ask(1).disabled,
      'Deleting the question mark lost the hand-edited origin');
check(document.execCommand('undo') && field(1).value === 'Hold?' && !ask(1).disabled, 'Native Undo lost the hand edit');
check(document.execCommand('redo') && field(1).value === 'Hold' && ask(1).disabled,
      'Native Redo made matching fill text askable');
card(1).querySelector('[data-choice="Merge"]').click();
check(field(1).value === 'Merge' && ask(1).disabled, 'A replacement option remained askable');
undo.click();
check(field(1).value === 'Hold' && ask(1).disabled, 'Undo changes made matching fill text askable');
card(1).querySelector('[data-choice="Hold"]').click();
field(1).focus();
field(1).select();
check(document.execCommand('insertText', false, 'Merge'), 'Manual replacement failed');
field(1).setSelectionRange(0, 0);
check(document.execCommand('insertText', false, '?'), 'Separate manual edit failed');
check(document.execCommand('undo') && field(1).value === 'Merge' && !ask(1).disabled,
      'Native Undo classified a manually typed option label as a selected answer');
check(document.execCommand('redo') && field(1).value === '?Merge' && !ask(1).disabled,
      'Native Redo lost the manually typed draft');
""")

    def test_native_undo_of_a_suggestion_ignores_sibling_history(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.check_page(port, r"""
field(2).focus();
check(document.execCommand('insertText', false, 'Sibling draft'), 'Sibling edit failed');
document.getElementById('suggest').click();
const ask = card(1).querySelector('.ask-button');
field(1).focus();
field(1).setSelectionRange(5, 5);
check(document.execCommand('insertText', false, '?') && !ask.disabled, 'Editing the suggestion did not enable Ask');
check(document.execCommand('undo') && field(1).value === 'Merge' && ask.disabled,
      'Native Undo made the restored suggestion askable');
check(field(2).value === 'Sibling draft', 'Undo changed the sibling draft');
check(document.execCommand('redo') && field(1).value === 'Merge?' && !ask.disabled,
      'Native Redo did not restore the edited suggestion');
""")

    def test_undo_treats_restored_text_matching_its_fill_as_an_answer(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for suggested in (False, True):
            with self.subTest(suggested=suggested):
                self.check_page(port, r"""
const editor = field(1);
const ask = card(1).querySelector('.ask-button');
const option = card(1).querySelector('[data-choice="Hold"]');
const fill = suggested ? 'Merge' : 'Hold';
(suggested ? document.getElementById('suggest') : option).click();
check(editor.value === fill && ask.disabled, 'The original fill was askable');
editor.focus();
editor.setSelectionRange(fill.length, fill.length);
check(document.execCommand('insertText', false, '?'), 'The first manual edit failed');
editor.setSelectionRange(fill.length, fill.length + 1);
check(document.execCommand('delete') && editor.value === fill && !ask.disabled,
      'Deleting the punctuation did not preserve the manual draft');
editor.setSelectionRange(0, 0);
check(document.execCommand('insertText', false, '!'), 'The separate manual edit failed');
check(document.execCommand('undo') && editor.value === fill && ask.disabled,
      'Native Undo made matching manual text askable');
check(document.execCommand('undo') && editor.value === fill + '?' && !ask.disabled,
      'Native Undo lost the first manual edit');
check(document.execCommand('undo') && editor.value === fill && ask.disabled,
      'Native Undo made the actual fill askable');
check(document.execCommand('redo') && editor.value === fill + '?' && !ask.disabled,
      'Native Redo lost the first manual edit');
check(document.execCommand('redo') && editor.value === fill && ask.disabled,
      'Native Redo made matching manual text askable');
editor.setSelectionRange(0, 1);
check(document.execCommand('insertText', false, '!'), 'Editing the fill failed');
editor.setSelectionRange(0, 1);
check(document.execCommand('insertText', false, fill[0]) && editor.value === fill && !ask.disabled,
      'Retyping the fill did not enable Ask Firstmate');
(suggested ? card(1).querySelector('[data-choice="Merge"]') : option).click();
check(editor.value === fill && ask.disabled, 'Selecting the option did not replace the manual origin');
document.getElementById('undo').click();
check(editor.value === fill && !ask.disabled, 'Undo changes lost the saved hand-edited origin');
""", setup=f"const suggested = {json.dumps(suggested)};")

    def test_whitespace_around_fills_stays_an_answer_through_edits_and_history(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for suggested in (False, True):
            with self.subTest(suggested=suggested):
                self.check_page(port, r"""
const editor = field(1);
const ask = card(1).querySelector('.ask-button');
const fill = suggested ? 'Merge' : 'Hold';
(suggested ? document.getElementById('suggest') : card(1).querySelector('[data-choice="Hold"]')).click();
editor.focus();
editor.setSelectionRange(fill.length, fill.length);
check(document.execCommand('insertText', false, ' ') && editor.value === fill + ' ' && ask.disabled,
      'Adding only whitespace made the filled answer askable');
check(document.getElementById('send').textContent === 'Send 1 answer', 'Adding whitespace dropped the answer draft');
check(document.execCommand('undo') && editor.value === fill && ask.disabled, 'Undo made the original fill askable');
check(document.execCommand('redo') && editor.value === fill + ' ' && ask.disabled,
      'Redo made a draft askable that would send the unchanged answer');
editor.setSelectionRange(0, 0);
check(document.execCommand('insertText', false, '?') && !ask.disabled, 'Editing the fill did not enable Ask');
check(document.execCommand('undo') && editor.value === fill + ' ' && ask.disabled,
      'Undo made a draft askable that would send the unchanged answer');
""", setup=f"const suggested = {json.dumps(suggested)};")

    def test_grouped_backspaces_undo_and_redo_classify_the_restored_text(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for suggested in (False, True):
            with self.subTest(suggested=suggested):
                self.check_page(port, r"""
const editor = field(1);
const ask = card(1).querySelector('.ask-button');
const fill = suggested ? 'Merge' : 'Hold';
(suggested ? document.getElementById('suggest') : card(1).querySelector('[data-choice="Hold"]')).click();
editor.focus();
editor.setSelectionRange(fill.length, fill.length);
check(document.execCommand('insertText', false, '??'), 'The initial edit failed');
const shortened = fill.slice(0, -1);
for (const expected of [fill + '?', fill, shortened]) {
  check(document.execCommand('delete') && editor.value === expected, 'Backspace did not produce ' + expected);
}
check(document.execCommand('undo') && editor.value === fill + '??' && !ask.disabled,
      'The first Undo did not restore the grouped deletion as one edit');
check(document.execCommand('undo') && editor.value === fill && ask.disabled,
      'Undo made the restored fill askable');
check(document.execCommand('redo') && editor.value === fill + '??' && !ask.disabled,
      'Redo did not restore the askable draft');
check(document.execCommand('redo') && editor.value === shortened && !ask.disabled,
      'Redo did not restore the grouped deletion as one edit');
""", setup=f"const suggested = {json.dumps(suggested)};")

    def test_replacing_a_rejected_ask_allows_answer_edits_before_and_after_saving(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for edit_option in (False, True):
            with self.subTest(edit_option=edit_option):
                self.check_page(port, r"""
const answers = [];
window.fetch = async (url, options) => {
  if (url === 'asks') return {status: 400, text: async () => 'Check the question and ask.'};
  const {answer} = JSON.parse(options.body);
  answers.push(answer);
  return {ok: true, json: async () => ({state: 'answered', revision: 'new', delivery: 'sent',
    answer, answered_at: '2026-11-25T01:00:00Z'})};
};
const send = document.getElementById('send');
type(1, 'Show the preview.');
card(1).querySelector('.ask-button').click();
await settled();
check(send.disabled, 'The failed ask was queued as an answer');
if (editOption) card(1).querySelector('[data-choice="Hold"]').click();
const expected = editOption ? 'Hold for the pilot.' : 'Proceed with the pilot.';
type(1, expected);
check(!send.disabled, 'The rejected ask blocked a replacement answer');
send.click();
await settled();
check(answers.join() === expected && field(1).defaultValue === expected, 'The replacement answer was not saved');
type(1, 'Merge tomorrow.');
check(!send.disabled, 'The rejected ask blocked a new decision after saving');
send.click();
await settled();
check(answers.join() === expected + ',Merge tomorrow.', 'The answer batch kept stale ask intent');
""", setup=f"const editOption = {json.dumps(edit_option)};")

    def test_question_part_format_is_shared_by_cli_and_browser_commands(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        ask = self.ask_body(port, "Q4.1", "Show the preview.")
        self.assertEqual(self.post(port, "/qd/asks", ask)[0], 200)
        self.assertEqual(self.run_cli("reply", "q4.1", "--ask", ask["id"], "--text", "Here it is.")[0], 0)
        _, reply = self.messages("1", 3)
        self.assertEqual(self.run_cli("mark", "q4.2", "closed")[0], 0)
        before = self.store()
        for part in ("Q4.01", "Q4.0", "Q4.1.2"):
            with self.subTest(part=part):
                self.assertEqual(self.run_cli("mark", part, "answered", "--via", "chat")[0], 2)
                self.assertEqual(self.run_cli("reply", part, "--ask", ask["id"], "--text", "Wrong part.")[0], 2)
        for part in ("Q4.01", "Q4.0", "Q4.1.2", "q4.1"):
            with self.subTest(browser_part=part):
                self.assertEqual(self.post(port, "/qd/answers", {"part": part, "answer": "Hold", "revision": ""})[0], 400)
                self.assertEqual(self.post(port, "/qd/asks", {**ask, "part": part})[0], 400)
                self.assertEqual(self.post(port, "/qd/replies/read", {"part": part, "ids": [reply["id"]]})[0], 400)
        self.assertEqual(self.store(), before)

    def test_live_unread_reply_expands_a_folded_answer_and_keeps_messages_open(self) -> None:
        home = self.inbox()
        port = self.start("/qd", home=home, ask_home=home)
        self.assertEqual(self.ask(port, "Q4.1", "Show a preview.")[0], 200)
        status, answer = self.post(port, "/qd/answers", {"part": "Q4.1", "answer": "Merge", "revision": ""})
        self.assertEqual((status, answer["delivery"]), (200, "sent"))
        page = self.fetch(port, "/qd/q4.html")[1]
        (ask,) = self.messages("1", 3)
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", ask["id"], "--text", "The preview is ready.")[0], 0)
        status, replacement = self.ask(port, "Q4.1", "Can you show another layout?")
        self.assertEqual(status, 200)
        replacement_id = self.messages("1", 3)[-1]["id"]
        self.check_page(port, r"""
const decision = card(1);
const row = decision.closest('details.done');
const messages = decision.querySelector('.thread');
check(row && !row.open, 'The saved answer did not start folded');
let respond;
const calls = [];
window.fetch = (url, options) => {
  calls.push(url);
  const {id} = JSON.parse(options.body);
  return new Promise(resolve => {
    respond = () => resolve({ok: true, status: 200, json: async () => ({...replacement,
      thread: replacement.thread.replace(replacementId, id)})});
  });
};
row.open = true;
type(1, 'Can you show another layout?');
decision.querySelector('.ask-button').click();
check(respond && messages.open, 'Sending the follow-up did not open Messages');
row.open = false;
respond();
await settled();
const badge = decision.querySelector('.badge');
const reply = messages.querySelector('[data-reply-id][data-unread] .record');
check(!decision.closest('details.done') && badge.textContent === 'Replied' && badge.getClientRects().length,
      'The new reply badge stayed inside the folded answer');
check(messages.open && reply && reply.textContent === 'The preview is ready.' && reply.getClientRects().length,
      'The new unread reply was not visible in the open Messages panel');
check(calls.join() === 'asks', 'The live update acknowledged the reply without an explicit opening');
""", page=page, setup=f"const [replacement, replacementId] = {json.dumps([replacement, replacement_id])};")

    def test_acknowledgment_reconciles_both_orders_of_live_thread_replacement(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.ask(port, "Q4.1", "First ask.")
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", self.messages("1", 3)[0]["id"], "--text", "First reply.")[0], 0)
        _, first = self.messages("1", 3)
        page = self.fetch(port, "/qd/q4.html")[1]
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", first["ask"], "--text", "Arrived later.")[0], 0)
        _, _, later = self.messages("1", 3)
        replacement = self.ask(port, "Q4.1", "Another ask.")[1]
        replacement_id = self.messages("1", 3)[-1]["id"]
        for replace_first in (True, False):
            with self.subTest(replace_first=replace_first):
                data = json.dumps([first["id"], later["id"], replacement, replacement_id, replace_first])
                setup = f"const [first, later, replacement, replacementId, replaceFirst] = {data};"
                self.check_page(port, r"""
const calls = [];
const complete = {};
window.fetch = (url, options) => new Promise(resolve => {
  calls.push([url, JSON.parse(options.body)]);
  complete[url] = body => resolve({ok: true, status: 200, json: async () => body});
});
const decision = card(1);
decision.querySelector('summary').click();
type(1, 'Another ask.');
decision.querySelector('.ask-button').click();
check(calls.length === 2 && calls[0][0] === 'replies/read' && calls[0][1].ids.join() === first,
      'The explicit opening acknowledged the wrong reply');
const askResponse = {...replacement, thread: replacement.thread.replace(replacementId, calls[1][1].id)};
const finishAsk = () => complete.asks(askResponse);
const finishRead = () => complete['replies/read']({replies: {[first]: true, [later]: false}});
if (replaceFirst) finishAsk(); else finishRead();
await settled();
if (replaceFirst) finishRead(); else finishAsk();
await settled();
check(calls.length === 2, 'Live replacement acknowledged a reply automatically');
const unread = [...decision.querySelectorAll('[data-unread]')].map(reply => reply.dataset.replyId);
check(unread.join() === later && decision.querySelector('.badge').textContent === 'Replied',
      'Stale snapshot overwrote the acknowledgment or hid a newer reply');
""", page=page, setup=setup)

    def test_late_acknowledgment_cannot_restore_a_reply_read_in_another_tab(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.assertEqual(self.ask(port, "Q4.1", "Show me.")[0], 200)
        (ask,) = self.messages("1", 3)
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", ask["id"], "--text", "First reply.")[0], 0)
        page = self.fetch(port, "/qd/q4.html")[1]
        self.assertEqual(self.run_cli("reply", "Q4.1", "--ask", ask["id"], "--text", "Second reply.")[0], 0)
        _, first, second = self.messages("1", 3)
        endpoint = "/qd/replies/read"
        first_request = {"part": "Q4.1", "ids": [first["id"]]}
        old = self.post(port, endpoint, first_request)[1]
        self.post(port, endpoint, {"part": "Q4.1", "ids": [second["id"]]})
        new = self.post(port, endpoint, first_request)[1]
        for old_first in (True, False):
            with self.subTest(old_first=old_first):
                data = json.dumps([first_request, old, new, old_first])
                self.check_page(port, r"""
const requests = [];
const complete = [];
window.fetch = (url, options) => new Promise(resolve => {
  requests.push(JSON.parse(options.body));
  complete.push(body => resolve({ok: true, json: async () => body}));
});
const summary = card(1).querySelector('summary');
summary.click();
summary.click();
summary.click();
check(requests.length === 2 && requests.every(request => JSON.stringify(request) === JSON.stringify(firstRequest)),
      'Reopening messages acknowledged a reply outside this tab');
if (oldFirst) complete[0](old); else complete[1](newer);
await settled();
check(card(1).querySelector('.badge').textContent === (oldFirst ? 'Replied' : 'Open'),
      'The first response reported the wrong reply status');
check(document.querySelector('.progress').textContent.includes('Replied') === oldFirst,
      'Question progress did not reflect the unread reply');
if (oldFirst) complete[1](newer); else complete[0](old);
await settled();
check(card(1).querySelector('.badge').textContent === 'Open', 'The badge did not settle on the read state');
""", page=page, setup=f"const [firstRequest, old, newer, oldFirst] = {data};")


class TalkToFirstmateTests(ImageCase):
    """A conversation about any topic: its own saved item, asks that name the item, replies in time order."""

    start = ServeTests.start
    fetch = ServeTests.fetch
    post = ConversationTests.post
    inbox = ConversationTests.inbox

    def begin(self, port, title="Harbor lamp colours", text="Which amber suits the pier lamps?", **changes):
        page = self.fetch(port, "/qd/")[1]
        match = re.search(r'data-home="([^"]+)"', page)
        body = {"id": uuid.uuid4().hex, "title": title, "text": text, "home": match[1] if match else ""}
        return body, self.post(port, "/qd/conversations", {**body, **changes})

    def thread(self, item=0):
        return self.store()["items"][item]["conversation"]["item"]

    def test_a_new_conversation_is_its_own_item_and_reaches_the_inbox_as_a_conversation_message(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        body, (status, result) = self.begin(port)
        self.assertEqual(status, 200)
        self.assertEqual(result, {"href": "q1.html", "message": "Message sent."})
        (item,) = self.store()["items"]
        self.assertEqual((item["kind"], item["title"], item["body"]), ("conversation", "Harbor lamp colours", {}))
        self.assertNotIn(body["text"], item["description"])
        (message,) = self.thread()
        self.assertEqual((message["id"], message["author"], message["delivery"]), (body["id"], "you", "sent"))
        self.assertEqual((home / "notes").read_text(),
                         f"CONVERSATION Q1 {body['id']} Topic: Harbor lamp colours\nWhich amber suits the pier lamps?\n")
        self.assertEqual(self.run_cli("list")[1].split()[1], "conversation")
        self.assertIn("Conversation", self.run_cli("show", "Q1")[1])
        self.assertEqual(self.run_cli("classify", "Q1", "--project", "harbor-signs", "--tag", "product-design")[0], 0)
        self.assertEqual(self.store()["items"][0]["project"], "harbor-signs")

    def test_firstmate_opens_a_conversation_and_posts_updates_without_an_inbox_note(self) -> None:
        home = self.inbox()
        code, out, err = self.run_cli("converse", "--title", "Tide work", "--text", "Started the tide job.",
                                      "--link-text", "Plan", "--url", "https://example.com/plan",
                                      "--image", str(self.red), "--alt", "A red quay")
        self.assertEqual((code, out, err), (0, "Q1\n", ""))
        (first,) = self.thread()
        self.assertEqual((first["author"], first["read"], "ask" in first, first["links"][0]["text"]), ("firstmate", False, False, "Plan"))
        self.assertNotIn("home", first)
        self.assertTrue((quarterdeck.store_path().parent / "reply-images" / "q1" / first["images"][0]["file"]).is_file())

        port = self.start("/qd", ask_home=home)

        def assert_replied_on_desk():
            status, page, _ = self.fetch(port, "/qd/")
            self.assertEqual(status, 200)
            desk = page.partition('<div class="desk"><table>')[2].partition("</table>")[0]
            rows = [row for row in re.findall(r"<tr\b[^>]*>.*?</tr>", desk, re.DOTALL) if 'href="q1.html"' in row]
            self.assertEqual(len(rows), 1)
            self.assertIn('<span class="badge">Replied</span>', rows[0])

        assert_replied_on_desk()
        self.assertEqual(self.post(port, "/qd/replies/read", {"part": "Q1", "ids": [first["id"]]})[0], 200)
        status, page, _ = self.fetch(port, "/qd/")
        self.assertEqual(status, 200)
        self.assertNotIn('<div class="desk"><table>', page)
        self.assertIn('href="q1.html"', page)

        code, out, err = self.run_cli("post", "q1", "--text", "Halfway there.",
                                      "--link-text", "Progress", "--url", "https://example.com/progress",
                                      "--image", str(self.blue), "--alt", "A blue quay")
        self.assertEqual((code, out, err), (0, "Q1 Updated\n", ""))
        _, update = self.thread()
        self.assertEqual((update["text"], update["read"], "ask" in update), ("Halfway there.", False, False))
        self.assertEqual(update["links"], [{"text": "Progress", "url": "https://example.com/progress"}])
        self.assertEqual(update["images"][0]["alt"], "A blue quay")
        self.assertTrue((quarterdeck.store_path().parent / "reply-images" / "q1" / update["images"][0]["file"]).is_file())
        assert_replied_on_desk()

        shown = self.run_cli("show", "Q1")[1]
        self.assertEqual(shown.count("Update"), 2)
        self.assertNotIn("In reply to", shown)
        page = self.fetch(port, "/qd/q1.html")[1]
        self.assertEqual(page.count("Firstmate update"), 2)
        self.assertNotIn("In reply to", page)
        self.assertIn('id="send-message"', page)
        self.assertEqual(self.post(port, "/qd/replies/read", {"part": "Q1", "ids": [update["id"]]})[0], 200)
        self.assertFalse(any(m["read"] is False for m in self.thread()))
        self.assertFalse((home / "notes").exists())
        # The owner continues it as any conversation, and Firstmate replies to that message as usual.
        page = self.fetch(port, "/qd/q1.html")[1]
        ask = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Thanks, which tide?",
               "home": re.search(r'id="send-message" data-home="([^"]+)"', page)[1]}
        self.assertEqual(self.post(port, "/qd/asks", ask)[1]["message"], "Message sent.")
        self.assertEqual((home / "notes").read_text(), f"CONVERSATION Q1 {ask['id']} Topic: Tide work\nThanks, which tide?\n")
        self.assertEqual(self.run_cli("reply", "Q1", "--ask", ask["id"], "--text", "The spring tide.")[0], 0)
        self.assertEqual(self.thread()[-1]["ask"], ask["id"])
        self.assertIn("In reply to message 1", self.fetch(port, "/qd/q1.html")[1])
        replacement = self.inbox(name="replacement")
        other_port = self.start("/qd", ask_home=replacement)
        other_binding = re.search(r'data-home="([^"]+)"', self.fetch(other_port, "/qd/")[1])[1]
        follow_up = {**ask, "id": uuid.uuid4().hex, "home": other_binding}
        self.assertEqual(self.post(other_port, "/qd/asks", follow_up)[0], 409)
        self.assertFalse((replacement / "attempts").exists())

    def test_firstmate_conversation_binds_before_the_first_owner_message(self) -> None:
        original = self.inbox(accept=False)
        replacement = self.inbox(name="replacement")
        alias = self.root / "current"
        config = self.root / "explicit.json"
        config.write_text(json.dumps({"firstmate_home": str(alias)}))
        command = ("converse", "--title", "Tide work", "--text", "Started the tide job.", "--config", str(config))
        code, out, err = self.run_cli(*command)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("firstmate_home must resolve to an existing directory", err)
        self.assertFalse(quarterdeck.store_path().exists())

        alias.symlink_to(original, target_is_directory=True)
        code, out, err = self.run_cli(*command)
        self.assertEqual((code, out, err), (0, "Q1\n", ""))
        self.assertFalse((original / "attempts").exists())

        original_port = self.start("/qd", ask_home=original)
        original_page = self.fetch(original_port, "/qd/q1.html")[1]
        binding = re.search(r'id="send-message" data-home="([^"]+)"', original_page)[1]
        ask = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Which tide?", "home": binding}
        alias.unlink()
        alias.symlink_to(replacement, target_is_directory=True)
        with self.serve_cli("--config", str(config), "--base-path", "/qd") as port:
            page = self.fetch(port, "/qd/q1.html")[1]
            other_binding = re.search(r'data-home="([^"]+)"', self.fetch(port, "/qd/")[1])[1]
            self.assertIn("Restore the original firstmate_home", page)
            self.assertNotIn('id="send-message"', page)
            rebound = {**ask, "home": other_binding}
            refusal = (409, {"message": "Firstmate home changed. This ask belongs to the original home.",
                             "saved": False})
            self.assertEqual(self.post(port, "/qd/asks", rebound), refusal)
            self.assertEqual(self.post(port, "/qd/asks", {**rebound, "action": "retry"}), refusal)
            self.assertEqual(len(self.thread()), 1)

            self.assertEqual(self.post(original_port, "/qd/asks", ask)[1]["message"],
                             "Message saved. Not delivered.")
            retry = {"part": "Q1", "id": ask["id"], "action": "retry"}
            self.assertEqual(self.post(port, "/qd/asks", retry), (409, {**refusal[1], "saved": True}))
            self.assertFalse((replacement / "attempts").exists())
            self.inbox()
            self.assertEqual(self.post(original_port, "/qd/asks", retry)[1]["message"], "Message sent.")
            self.assertEqual((original / "notes").read_text(),
                             f"CONVERSATION Q1 {ask['id']} Topic: Tide work\nWhich tide?\n")

    def test_post_and_converse_reject_bad_input_and_change_nothing(self) -> None:
        self.assertEqual(self.run_cli("converse", "--title", "Tide work", "--text", "Hello.")[0], 0)
        self.assertEqual(self.run_cli("add", "--kind", "list", "--title", "T", "--description", "D", "--body-file", "-",
                                      stdin=json.dumps({"entries": ["a"]}))[0], 0)
        before = self.store()
        for argv in (("post", "Q2", "--text", "No."), ("post", "Q9", "--text", "No."), ("post", "Q1.1", "--text", "No."),
                     ("post", "Q1", "--text", " "), ("post", "Q1", "--text", "a", "--url", "https://example.com"),
                     ("post", "Q1", "--text", "a", "--image", str(self.red)),
                     ("converse", "--title", " ", "--text", "a"), ("converse", "--title", "a", "--text", "a", "--alt", "x")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli(*argv)[0], 2)
        self.assertEqual(self.store(), before)

    def test_the_desk_offers_talk_to_firstmate_only_with_a_home(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for path in ("/qd/", "/qd/index.html"):
            _, page, headers = self.fetch(port, path)
            self.assertIn('id="open-talk"', page)
            self.assertIn('Talk to Firstmate', page)
            self.assertIn('id="talk-form"', page)
            self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'none'")
        _, page, headers = self.fetch(self.start("/qd"), "/qd/")
        self.assertNotIn('id="open-talk"', page)
        self.assertNotIn("Content-Security-Policy", headers)

    def test_a_conversation_replays_once_and_retries_with_the_same_note(self) -> None:
        home = self.inbox(accept=False)
        port = self.start("/qd", ask_home=home)
        body, (status, result) = self.begin(port)
        self.assertEqual((status, result), (200, {"href": "q1.html", "message": "Message saved. Not delivered."}))
        (first,) = self.thread()
        self.assertIn("Send message again", self.fetch(port, "/qd/q1.html")[1])
        self.assertEqual(self.post(port, "/qd/conversations", body), (status, result))
        self.assertEqual(len(self.store()["items"]), 1)
        self.assertEqual(self.post(port, "/qd/conversations", {**body, "text": "Another."})[0], 409)
        self.assertEqual(self.post(port, "/qd/conversations", {**body, "title": "Another"})[0], 409)
        self.inbox()
        self.assertEqual(self.post(port, "/qd/conversations", body),
                         (200, {"href": "q1.html", "message": "Message sent."}))
        self.assertEqual(self.thread(), [{**first, "delivery": "sent"}])
        self.assertEqual((home / "notes").read_text(), f"CONVERSATION Q1 {body['id']} Topic: {body['title']}\n{body['text']}\n")
        retry = {"part": "Q1", "id": body["id"], "action": "retry"}
        self.assertEqual(self.post(port, "/qd/asks", retry)[1]["message"], "Message sent.")
        self.assertEqual(len(self.store()["items"]), 1)
        self.assertEqual(len((home / "notes").read_text().splitlines()), 2)
        self.assertEqual((home / "attempts").read_text().splitlines(), [first["request_id"]] * 3)

    def test_conversations_wait_for_a_missing_home_alias_to_return(self) -> None:
        home = self.root / "home"
        home.mkdir()
        alias = self.root / "current"
        port = self.start("/qd", ask_home=alias)
        self.assertNotIn('id="open-talk"', self.fetch(port, "/qd/")[1])
        self.assertEqual(self.begin(port)[1][0], 403)
        self.assertFalse(quarterdeck.store_path().exists())

        alias.symlink_to(home, target_is_directory=True)
        body, result = self.begin(port)
        self.assertEqual(result, (200, {"href": "q1.html", "message": "Message saved. Not delivered."}))
        (first,) = self.thread()
        before = quarterdeck.store_path().read_bytes()
        alias.unlink()
        alias.symlink_to(self.root / "missing", target_is_directory=True)
        self.assertNotIn('id="open-talk"', self.fetch(port, "/qd/")[1])
        page = self.fetch(port, "/qd/q1.html")[1]
        self.assertIn(body["text"], page)
        self.assertNotIn('id="send-message"', page)
        self.assertNotIn('data-retry-ask=', page)
        follow_up = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Which green?", "home": body["home"]}
        retry = {"part": "Q1", "id": body["id"], "action": "retry"}
        for endpoint, payload in (("conversations", body), ("asks", follow_up), ("asks", retry)):
            with self.subTest(endpoint=endpoint, payload=payload):
                self.assertEqual(self.post(port, f"/qd/{endpoint}", payload)[0], 403)
        self.assertEqual(quarterdeck.store_path().read_bytes(), before)

        self.inbox()
        alias.unlink()
        alias.symlink_to(home, target_is_directory=True)
        self.assertIn('id="send-message"', self.fetch(port, "/qd/q1.html")[1])
        self.assertEqual(self.post(port, "/qd/conversations", body),
                         (200, {"href": "q1.html", "message": "Message sent."}))
        self.assertEqual(self.thread(), [{**first, "delivery": "sent"}])
        self.assertEqual((home / "attempts").read_text().splitlines(), [first["request_id"]])

    def test_bad_conversations_save_nothing(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        for changes in ({"title": "  "}, {"text": ""}, {"id": "nope"}, {"title": "x" * 201}, {"title": 3}):
            self.assertEqual(self.begin(port, **changes)[1][0], 400, changes)
        self.assertEqual(self.begin(port, home="0" * 64)[1][0], 409)
        self.assertEqual(self.post(port, "/qd/conversations", {"id": uuid.uuid4().hex, "title": "T"})[0], 400)
        self.assertEqual(self.post(port, "/qd/conversations", {}, origin="http://example.com")[0], 403)
        self.assertFalse(quarterdeck.store_path().exists())
        self.assertEqual(self.post(self.start("/qd"), "/qd/conversations", {"id": "a" * 32, "title": "T", "text": "M", "home": ""})[0], 403)
        self.assertFalse((home / "attempts").exists())

    def test_more_messages_and_replies_show_in_time_order_with_replied_until_opened(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        first, _ = self.begin(port)
        binding = first["home"]
        second = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Also, which green?", "home": binding}
        self.assertEqual(self.post(port, "/qd/asks", second)[1]["message"], "Message sent.")
        self.assertEqual((home / "notes").read_text().splitlines()[-2:],
                         [f"CONVERSATION Q1 {second['id']} Topic: Harbor lamp colours", "Also, which green?"])
        self.assertEqual(self.run_cli("reply", "Q1", "--ask", second["id"], "--text", "Sea green.")[0], 0)
        self.assertEqual(self.run_cli("reply", "Q1", "--ask", first["id"], "--text", "Warm amber.")[0], 0)
        self.assertEqual(self.run_cli("post", "Q1", "--text", "The lamps are installed."), (0, "Q1 Updated\n", ""))
        self.assertEqual(self.run_cli("reply", "Q1", "--ask", "f" * 32, "--text", "Lost.")[0], 2)
        self.assertEqual(self.run_cli("reply", "Q1.1", "--ask", first["id"], "--text", "Lost.")[0], 2)
        text = ["Which amber suits the pier lamps?", "Also, which green?", "Sea green.", "Warm amber.",
                "The lamps are installed."]
        self.assertEqual([m["text"] for m in self.thread()], text)
        page = self.fetch(port, "/qd/q1.html")[1]
        status, transcript, err = self.run_cli("show", "Q1")
        self.assertEqual(status, 0, err)
        self.assertEqual([transcript.index(t) for t in text], sorted(transcript.index(t) for t in text))
        self.assertIn(f"{first['id']}  Message 1\n  {first['text']}", transcript)
        self.assertIn(f"{second['id']}  Message 2\n  {second['text']}", transcript)
        self.assertIn("In reply to message 2\n  Sea green.", transcript)
        self.assertIn("In reply to message 1\n  Warm amber.", transcript)
        self.assertIn("Update\n  The lamps are installed.", transcript)
        self.assertIn("Content-Security-Policy", self.fetch(port, "/qd/q1.html")[2])
        self.assertIn('id="send-message"', page)
        self.assertNotIn('class="lead"', page)
        desk = self.fetch(port, "/qd/")[1]
        self.assertIn('<td>None open</td><td><span class="badge">Replied</span></td>', desk)
        self.assertIn('<span class="badge">Replied</span>', self.fetch(port, "/qd/")[1])
        ids = [m["id"] for m in self.thread() if m["author"] == "firstmate"]
        self.assertEqual(self.post(port, "/qd/replies/read", {"part": "Q1", "ids": ids})[0], 200)
        desk = self.fetch(port, "/qd/")[1]
        self.assertNotIn('<td>None open</td>', desk)
        self.assertIn("No questions need your attention.", desk)
        status, sent = self.post(port, "/qd/asks", {**second, "id": uuid.uuid4().hex, "text": "Thanks."})
        self.assertEqual((status, sent["message"]), (200, "Message sent."))
        status, retried = self.post(port, "/qd/asks", {"part": "Q1", "id": second["id"], "action": "retry"})
        self.assertEqual((status, retried["message"]), (200, "Message sent."))
        for path, markup in (("open", page), ("send", sent["thread"]), ("retry", retried["thread"])):
            with self.subTest(path=path):
                self.assertEqual([markup.index(t) for t in text], sorted(markup.index(t) for t in text))
                self.assertRegex(markup, rf'data-ask-id="{first["id"]}" id="message-1"[^>]*>[^\n]*<span>Message 1</span>')
                self.assertRegex(markup, rf'data-ask-id="{second["id"]}" id="message-2"[^>]*>[^\n]*<span>Message 2</span>')
                self.assertIn('<a href="#message-2">In reply to message 2</a></p><p class="record">Sea green.</p>', markup)
                self.assertIn('<a href="#message-1">In reply to message 1</a></p><p class="record">Warm amber.</p>', markup)
                self.assertIn('<span>Firstmate update</span></p><p class="record">The lamps are installed.</p>', markup)
                self.assertEqual(markup.count(">In reply to message "), 2)

    def test_a_conversation_cannot_send_messages_to_a_replacement_home(self) -> None:
        home = self.inbox()
        original_port = self.start("/qd", ask_home=home)
        first, _ = self.begin(original_port)
        self.inbox(accept=False)
        pending = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Which green?", "home": first["home"]}
        self.assertEqual(self.post(original_port, "/qd/asks", pending)[1]["message"], "Message saved. Not delivered.")
        original_page = self.fetch(original_port, "/qd/q1.html")[1]
        self.assertIn('id="send-message"', original_page)
        self.assertIn(f'data-retry-ask="{pending["id"]}"', original_page)
        before = quarterdeck.store_path().read_bytes()
        replacement = self.inbox(name="replacement")
        port = self.start("/qd", ask_home=replacement)
        binding = re.search(r'data-home="([^"]+)"', self.fetch(port, "/qd/")[1])[1]
        self.assertNotEqual(binding, first["home"])
        for readonly_port, notice in ((port, "Restore the original firstmate_home"),
                                      (self.start("/qd"), "Set firstmate_home")):
            status, page, _ = self.fetch(readonly_port, "/qd/q1.html")
            self.assertEqual(status, 200)
            self.assertIn(notice, page)
            self.assertIn(pending["text"], page)
            self.assertIn("Not delivered", page)
            self.assertNotIn('id="send-message"', page)
            self.assertNotIn('data-retry-ask=', page)
        for endpoint, request, saved in (
            ("asks", {**pending, "id": uuid.uuid4().hex, "home": binding}, False),
            ("asks", {**pending, "home": binding}, True),
            ("asks", {"part": "Q1", "id": pending["id"], "action": "retry"}, True),
            ("conversations", {**first, "home": binding}, True),
        ):
            with self.subTest(endpoint=endpoint, request=request):
                status, result = self.post(port, f"/qd/{endpoint}", request)
                self.assertEqual((status, result["saved"]), (409, saved))
                self.assertIn("Firstmate home changed", result["message"])
        self.assertEqual(quarterdeck.store_path().read_bytes(), before)
        self.assertFalse((replacement / "attempts").exists())
        self.assertEqual(self.begin(port, "New home topic")[1][1]["message"], "Message sent.")

    def test_conversations_are_listed_on_the_served_desk_and_excluded_from_exports(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        first, _ = self.begin(port)
        self.begin(port, "Quay sign colours", "Navy or black lettering?")
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            data["items"][0]["description"] = first["text"]
        self.assertEqual(self.run_cli("reply", "Q1", "--ask", first["id"], "--text", "Use golden amber.",
                                      "--link-text", "Colour chart", "--url", "https://example.org/chart",
                                      "--image", str(self.red), "--alt", "A red lamp")[0], 0)
        desk = self.fetch(port, "/qd/")[1]
        self.assertIn('id="tab-conversations"', desk)
        self.assertIn('<td>None open</td><td><span class="badge">Replied</span></td>', desk)
        panel = desk[desk.index('id="panel-conversations"'):]
        self.assertLess(panel.index("Quay sign colours"), panel.index("Harbor lamp colours"))
        for private in (first["text"], "Navy or black lettering?", "Use golden amber.", "https://example.org/chart",
                        "A red lamp"):
            self.assertNotIn(private.casefold(), desk.casefold())
        page = self.fetch(port, "/qd/q1.html")[1]
        for private in (first["text"], "Use golden amber.", "https://example.org/chart", "A red lamp"):
            self.assertIn(private, page)
        self.add("resource", "Public chart", "A reference.", "--url", "https://example.org", "--link-text", "Open")
        output = self.root / "site" / "index.html"
        output.parent.mkdir()
        (output.parent / "q1.html").write_text(page)
        self.assertEqual(self.run_cli("render", "--output", str(output))[0], 0)
        exported = output.read_text()
        self.assertIn("No questions need your attention.", exported)
        for private in ("<h2>Conversations</h2>", "Harbor lamp colours", "Quay sign colours", first["text"]):
            self.assertNotIn(private, exported)
        self.assertIn('href="q3.html"', exported)
        self.assertEqual({path.name for path in output.parent.glob("*.html")}, {"index.html", "q3.html"})

    check_answer_page = ServeTests.check_answer_page

    def test_conversation_search_matches_id_shaped_topics_and_exact_item_ids(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.begin(port, "Q5.3", "Hidden message")
        self.assertEqual(self.begin(port, "Other\u0085topic", "Q5.3")[1][0], 200)
        self.check_answer_page(self.start("/qd"), r"""
button('open-library').click();
button('tab-conversations').click();
const search = button('search-conversations');
const find = query => {
  search.value = query;
  search.dispatchEvent(new Event('input', {bubbles: true}));
  return [...document.querySelectorAll('#conversations-list .lib-item:not([hidden])')].map(row => row.dataset.id).join(',');
};
check(find('q5.3') === 'Q1', 'The ID-shaped topic was missed or a message matched');
check(find('Q2') === 'Q2', 'An exact conversation item ID did not match');
check(find('hidden message') === '', 'Message content matched a search');
check(find('other topic') === 'Q2', 'A plain topic did not match');
check(find('Other\u0085topic') === 'Q2', 'The pasted topic did not normalize like the saved topic');
check(find('\u0085Q2\u0085') === 'Q2', 'Whitespace prevented an exact item ID match');
""", path="/qd/", viewport=(390, 844))

    def test_the_pages_send_messages_and_start_conversations_in_a_browser(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.begin(port)
        stub = r"""
const sent = [];
window.fetch = async (path, init) => {
  sent.push([path, JSON.parse(init.body)]);
  return {ok:true, status:200, json: async () => ({thread:'<ol class="exchange"><li>stub</li></ol>', message:'Message sent.', href:'q9.html'})};
};
"""
        self.check_answer_page(port, stub + r"""
const box = document.getElementById('message-Q1');
const send = button('send-message');
check(send.disabled, 'Send message was ready with no text');
box.value = '  Also, which green?  ';
box.dispatchEvent(new Event('input', {bubbles: true}));
check(!send.disabled, 'Send message stayed disabled');
send.click();
await new Promise(resolve => setTimeout(resolve, 50));
check(sent.length === 1 && sent[0][0] === 'asks' && sent[0][1].part === 'Q1' && sent[0][1].text === 'Also, which green?'
      && /^[0-9a-f]{32}$/.test(sent[0][1].id), 'The message was not posted as expected');
check(box.value === '' && send.disabled, 'The sent message stayed in the box');
check(document.querySelector('.thread-messages').textContent === 'stub', 'The conversation was not refreshed');
check(document.querySelector('output').textContent === 'Message sent.', 'The status was not shown');
""", path="/qd/q1.html", viewport=(390, 844))
        self.check_answer_page(port, r"""
const seen = [];
const posts = [];
window.fetch = async (path, init) => {
  const payload = JSON.parse(init.body);
  if (path === 'replies/read') {
    check(payload.part === 'Q1', 'The reply acknowledgment targeted the wrong conversation');
    check(document.querySelector('.badge').textContent === 'Replied', 'The badge missed a newly visible reply');
    seen.push(...payload.ids);
    const unseen = seen.length === 1 ? 'reply-two' : 'reply-three';
    return {ok:true, json:async () => ({replies:{[unseen]:false, ...Object.fromEntries(seen.map(id => [id, true]))}})};
  }
  check(path === 'asks', 'Unexpected conversation request');
  posts.push(payload);
  const reply = id => `<p data-reply-id="${id}" data-unread>Firstmate replied.</p>`;
  const retry = '<button data-retry-ask="saved-message">Send message again</button>';
  return {ok:true, json:async () => ({thread: reply('reply-one') + (payload.action ? reply('reply-two') : retry),
    message: 'Message sent.'})};
};
const field = button('message-Q1');
field.value = 'A follow-up';
field.dispatchEvent(new Event('input', {bubbles:true}));
button('send-message').click();
await new Promise(resolve => setTimeout(resolve, 50));
check(seen.join(',') === 'reply-one', 'Sending did not acknowledge the newly displayed reply');
check(document.querySelector('.badge').hidden, 'The badge stayed visible even after acknowledgment');
document.querySelector('[data-retry-ask]').click();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts[1].action === 'retry' && posts[1].id === 'saved-message', 'The saved message was not retried');
check(seen.join(',') === 'reply-one,reply-two', 'Retry did not acknowledge only the newly displayed reply');
check(document.querySelector('.badge').hidden, 'The badge stayed visible after retry acknowledgment');
""", path="/qd/q1.html", viewport=(390, 844))
        self.check_answer_page(port, r"""
const sent = [];
window.fetch = async (path, init) => {
  sent.push([path, JSON.parse(init.body)]);
  return {ok:false, status:409, json:async () => ({message:'Firstmate home changed.', saved:false})};
};
const form = document.getElementById('talk-form');
check(form.hidden, 'The form showed before it was asked for');
button('open-talk').click();
check(!form.hidden && button('open-talk').getAttribute('aria-expanded') === 'true', 'Talk to Firstmate did not open the form');
const submit = form.querySelector('button[type="submit"]');
check(submit.disabled, 'Start conversation was ready with nothing typed');
button('talk-topic').value = 'Quay sign colours';
button('talk-message').value = 'Navy or black lettering?';
form.dispatchEvent(new Event('input', {bubbles: true}));
check(!submit.disabled, 'Start conversation stayed disabled');
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
check(sent.length === 1 && sent[0][0] === 'conversations' && sent[0][1].title === 'Quay sign colours'
      && sent[0][1].text === 'Navy or black lettering?', 'The conversation was not posted as expected');
const error = document.getElementById('talk-error');
check(!error.hidden && error.textContent === 'Firstmate home changed.', 'The refusal was not shown');
check(!submit.disabled && !button('talk-topic').disabled, 'The form stayed locked after a refusal');
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
check(sent.length === 2 && sent[1][1].id !== sent[0][1].id, 'An unsaved refusal kept the old request ID');
""", path="/qd/", viewport=(390, 844))

    def test_retrying_an_interrupted_follow_up_reuses_the_draft_and_requests_delivery(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.begin(port)
        self.check_answer_page(port, r"""
const posts = [];
window.fetch = async (path, init) => {
  check(path === 'asks', 'The composer posted to the wrong endpoint');
  posts.push(JSON.parse(init.body));
  if (posts.length === 1) throw new Error('Connection lost');
  if (posts.length === 2) {
    return {ok:false, status:409, json:async () => ({message:'Firstmate home changed.', saved:true})};
  }
  if (posts.length === 3) {
    return {ok:false, status:403, text:async () => 'Configure an available firstmate_home to ask Firstmate.'};
  }
  if (posts.length === 4) throw new Error('Connection lost');
  return {ok:true, json:async () => ({thread:'<p>A follow-up</p>', message:'Message sent.'})};
};
const field = button('message-Q1');
const send = button('send-message');
field.value = 'A follow-up';
field.dispatchEvent(new Event('input', {bubbles:true}));
send.click();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts.length === 1 && posts[0].action === undefined, 'The first send should be a new message');
check(field.value === 'A follow-up' && !send.disabled, 'Interruption lost the draft');
field.focus();
field.setSelectionRange(field.value.length, field.value.length);
check(document.execCommand('insertText', false, '?') && field.value === 'A follow-up?', 'Editing the draft failed');
check(document.execCommand('undo') && field.value === 'A follow-up', 'Undo did not restore the draft');
send.click();
await new Promise(resolve => setTimeout(resolve, 50));
const [first, retry] = posts;
check(posts.length === 2 && retry.action === 'retry', 'The second send did not request redelivery');
check(retry.id === first.id && retry.part === 'Q1' && retry.text === 'A follow-up' && retry.home === first.home,
      'The retry changed the original message');
check(!button('error-Q1').hidden && field.value === 'A follow-up' && !send.disabled, 'The saved refusal lost the draft');
send.click();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts.length === 3 && posts[2].id === first.id && posts[2].action === 'retry',
      'The saved refusal discarded the original message ID');
check(button('error-Q1').textContent.includes('firstmate_home') && field.value === 'A follow-up' && !send.disabled,
      'The unavailable home refusal lost the draft or hid the error');
const submit = async text => {
  field.value = text;
  field.dispatchEvent(new Event('input', {bubbles:true}));
  send.click();
  await new Promise(resolve => setTimeout(resolve, 50));
};
await submit('Different follow-up');
check(posts.length === 4 && posts[3].id !== first.id && posts[3].action === undefined,
      'A different draft reused the unresolved message ID');
await submit('A follow-up');
check(posts.length === 5 && posts[4].id === first.id && posts[4].action === 'retry',
      'Submitting a different message discarded the original unresolved ID');
check(field.value === '' && send.disabled, 'The confirmed draft stayed in the composer');
await submit('Different follow-up');
check(posts.length === 6 && posts[5].id === posts[3].id && posts[5].action === 'retry',
      'Confirming the first message cleared a different unresolved ID');
await submit('A follow-up');
check(posts.length === 7 && posts[6].id !== first.id && posts[6].action === undefined,
      'A new message reused a confirmed ID');
""", path="/qd/q1.html", viewport=(390, 844))

    def test_desk_replay_keeps_its_id_after_undo_in_either_field(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.check_answer_page(port, r"""
const posts = [];
window.fetch = async (path, init) => {
  check(path === 'conversations', 'The Desk posted to the wrong endpoint');
  posts.push(JSON.parse(init.body));
  if (posts.length === 2) {
    return {ok:false, status:409, json:async () => ({message:'Firstmate home changed.', saved:true})};
  }
  if (posts.length === 3) {
    return {ok:false, status:403, text:async () => 'Configure an available firstmate_home to ask Firstmate.'};
  }
  throw new Error('Connection lost');
};
button('open-talk').click();
const form = button('talk-form');
const topic = button('talk-topic');
const message = button('talk-message');
topic.value = 'Quay sign';
message.value = 'Navy or black?';
form.dispatchEvent(new Event('input', {bubbles:true}));
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
for (const field of [topic, message]) {
  const original = field.value;
  field.focus();
  field.setSelectionRange(original.length, original.length);
  check(document.execCommand('insertText', false, '!') && field.value === original + '!', 'Editing the draft failed');
  check(document.execCommand('undo') && field.value === original, 'Undo did not restore the draft');
}
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts.length === 2 && posts[1].id === posts[0].id, 'Undo discarded the interrupted creation ID');
check(!button('talk-error').hidden, 'The saved refusal was not shown');
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts.length === 3 && posts[2].id === posts[0].id, 'The saved refusal discarded the original creation ID');
check(button('talk-error').textContent.includes('firstmate_home'), 'The unavailable home refusal was not shown');
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts.length === 4 && posts[3].id === posts[0].id, 'The unavailable home refusal discarded the original creation ID');
topic.value = 'Quay  \u0085 sign';
topic.dispatchEvent(new Event('input', {bubbles:true}));
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts.length === 5 && posts[4].id === posts[0].id && posts[4].title === 'Quay sign',
      'Equivalent topic whitespace changed the creation request');
message.value = 'Use navy.';
message.dispatchEvent(new Event('input', {bubbles:true}));
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts.length === 6 && posts[5].id !== posts[0].id, 'Different content reused the prior request ID');
message.value = 'Navy or black?';
message.dispatchEvent(new Event('input', {bubbles:true}));
form.requestSubmit();
await new Promise(resolve => setTimeout(resolve, 50));
check(posts.length === 7 && posts[6].id === posts[0].id, 'Submitting a different topic or message discarded the unresolved ID');
""", path="/qd/", viewport=(390, 844))

    def test_reply_attachments_are_on_the_card_and_in_cli_transcripts(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        body, _ = self.begin(port)
        chart = "https://example.org/lamp-chart"
        code, _, err = self.run_cli("reply", "Q1", "--ask", body["id"], "--text", "See this.",
                                    "--link-text", "Lamp chart", "--url", chart,
                                    "--image", str(self.red), "--alt", "A red lamp")
        self.assertEqual(code, 0, err)
        (reply,) = [m for m in self.thread() if m["author"] == "firstmate"]
        image = f"reply-images/q1/{reply['images'][0]['file']}"
        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}/qd/{image}", timeout=5).status, 200)
        card = self.fetch(port, "/qd/q1.html")[1]
        self.assertIn("A red lamp", card)
        self.assertIn(chart, card)
        status, transcript, err = self.run_cli("show", "Q1")
        self.assertEqual(status, 0, err)
        self.assertIn(f"  See this.\n  Link: Lamp chart: {chart}\n  Image: A red lamp: {image}", transcript)
        status, replay = self.post(port, "/qd/conversations", body)
        self.assertEqual((status, replay), (200, {"href": "q1.html", "message": "Message sent."}))

    def test_add_cannot_save_a_conversation_and_help_names_the_conversation_note(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("add", "--kind", "conversation", "--title", "T", "--description", "D")
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            quarterdeck.main(["reply", "--help"])
        self.assertIn("CONVERSATION", out.getvalue())
        self.assertIn("owner's message in any conversation", " ".join(out.getvalue().split()))

    def test_a_damaged_conversation_item_is_refused(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.begin(port)
        valid = self.store()
        for damage in (lambda i: i.pop("conversation"), lambda i: i.update(body={"a": 1}),
                       lambda i: i["conversation"].update({"1": i["conversation"]["item"]})):
            data = json.loads(json.dumps(valid))
            damage(data["items"][0])
            quarterdeck.store_path().write_text(json.dumps(data))
            self.assertEqual(self.fetch(port, "/qd/")[0], 500)


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

    def test_local_state_is_ignored_and_force_staging_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.initialize_git(root)
            (root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
            for name, reason in (
                ("quarterdeck/items.json", "local Quarterdeck item store or lock"),
                ("nested/quarterdeck/items.json", "local Quarterdeck item store or lock"),
                ("nested/quarterdeck/items.json.lock", "local Quarterdeck item store or lock"),
                ("quarterdeck/visits.jsonl", "local Quarterdeck visit log"),
                ("nested/quarterdeck/visits.jsonl", "local Quarterdeck visit log"),
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Fictional decision context.")
                subprocess.run(["git", "check-ignore", "--", name], cwd=root, check=True, capture_output=True)
                subprocess.run(["git", "add", "-f", "--", name], cwd=root, check=True)
                self.assertIn(f"{name}: {reason}", privacy_guard.check_staged(root))

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
