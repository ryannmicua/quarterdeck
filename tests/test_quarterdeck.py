from __future__ import annotations

import io
import json
import os
import select
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
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
        self.root = Path(temp.name)
        patcher = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state"),
                                          "XDG_CONFIG_HOME": str(self.root / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch.object(sys, "stdin", io.StringIO(stdin)):
            code = quarterdeck.main(list(argv))
        return code, out.getvalue(), err.getvalue()

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

    def test_empty_quarterdeck_shows_a_plain_empty_state(self) -> None:
        pages = self.pages()
        self.assertEqual(set(pages), {"index.html"})
        self.assertIn("Nothing is saved yet.", pages["index.html"])
        self.assertNotIn("<table", pages["index.html"])

    def test_saved_page_lists_items_newest_first_with_their_fields(self) -> None:
        self.add_all_kinds()
        index = self.pages()["index.html"]
        rows = index
        self.assertLess(rows.index("Needs attention"), rows.index("References"))
        self.assertLess(rows.index(">Q3<"), rows.index(">Q2<"))
        self.assertLess(rows.index(">Q2<"), rows.index(">Q1<"))
        self.assertIn("Harbor chores", index)
        self.assertIn('<a href="q3.html">Tide launch decisions</a>', index)
        self.assertIn("Two open questions, saved to decide later.", index)
        for heading in ("ID", "Kind", "Title", "Description", "Saved"):
            self.assertIn(f'<th scope="col">{heading}</th>', index)

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
        self.assertNotIn(">https://example.com/lavish/calls<", pages["q1.html"])

    def test_text_is_escaped_and_no_server_path_appears(self) -> None:
        self.add("list", "<script>alert(1)</script>", "A & B", "--body-file", "-", stdin="<b>bold</b>")
        pages = self.pages()
        for page in pages.values():
            self.assertNotIn("<script>alert(1)</script>", page)
            self.assertNotIn(str(self.root), page)
            self.assertNotIn(str(Path.home()), page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", pages["q1.html"])


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
        self.assertIn('<a href="q3.html">Tide launch decisions</a>', output.read_text())
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
    def start(self, base_path: str = "") -> int:
        server = quarterdeck.make_server("127.0.0.1", 0, quarterdeck.make_handler("Quarterdeck", base_path))
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
        handler = quarterdeck.make_handler("Quarterdeck", "/qd", home=home)
        server = quarterdeck.make_server("127.0.0.1", 0, handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        answer = "Green & blue\nNo varnish."
        status, record = self.post_answer(port, "Q3.1", answer)
        self.assertEqual((200, "pending"), (status, record["delivery"]))
        self.assertIn("Not delivered", self.fetch(port, "/qd/q3.html")[1])
        saved = self.store()["items"][2]["answers"]["1"]
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True)
        script.write_text('#!/bin/sh\nset -eu\n'
                          'test "$#" = 6 && test "$1" = note && test "$2" = --request-id && '
                          'test "$4" = --json && test "$5" = -- || exit 2\n'
                          'test "$PWD" = "$FM_HOME"\n'
                          'printf "%s" "$6" >> "$3"\n')
        script.chmod(0o700)
        for _ in range(2):
            status, retry = self.post_answer(port, "Q3.1", answer)
            self.assertEqual((status, retry["delivery"]), (200, "sent"))
        self.assertEqual((home / saved["request_id"]).read_text(), "Q3.1 Green & blue\nNo varnish.")
        self.assertEqual(self.post_answer(port, "Q3.1", "Changed")[0], 409)
        status, correction = self.post_answer(port, "Q3.1", "Changed", record["revision"])
        self.assertEqual((status, correction["delivery"]), (200, "sent"))
        corrected = self.store()["items"][2]["answers"]["1"]
        self.assertNotEqual(saved["request_id"], corrected["request_id"])
        self.assertEqual((home / corrected["request_id"]).read_text(), "Q3.1 Changed")
        self.assertIn("Answer: Changed", self.run_cli("show", "Q3")[1])
        self.assertEqual(0, self.run_cli("mark", "Q3", "closed")[0])
        self.assertEqual(self.post_answer(port, "Q3.1", "New", correction["revision"])[0], 409)
        self.assertNotIn("Needs attention", self.fetch(port, "/qd/")[1])
        self.assertEqual(0, self.run_cli("mark", "Q3.2", "open")[0])
        self.assertIn("Needs attention", self.fetch(port, "/qd/")[1])

    def test_invalid_answers_leave_the_store_untouched(self):
        self.add_all_kinds()
        port = self.start("/qd")
        before = self.store()
        for part, answer in (("Q3.3", "Hold"), ("Q4.1", "Hold"), ("Q1.1", "Hold"), ("Q3.1", " "),
                             ("Q3.1", "a" * 20001)):
            self.assertEqual(self.post_answer(port, part, answer)[0], 400)
        for origin in ("", "null", "https://example.org"):
            self.assertEqual(self.post_answer(port, "Q3.1", "Hold", origin=origin)[0], 403)
        self.assertEqual(self.store(), before)

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

    def check_answer_page(self, port, script):
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the answer-card browser tests")
        status, page, _ = self.fetch(port, "/qd/q1.html")
        self.assertEqual(status, 200)
        fixture = self.root / "question.html"
        fixture.write_text(page.replace("</body>", """
<output id="browser-result">Incomplete</output>
<script>
const check = (condition, message) => { if (!condition) throw new Error(message); };
const field = n => document.getElementById('answer-Q1.' + n);
const card = n => document.getElementById('Q1.' + n);
const button = id => document.getElementById(id);
const typeAnswer = (n, text) => {
  field(n).value = text;
  field(n).dispatchEvent(new Event('input', {bubbles: true}));
};
(async () => {
""" + script + """
  button('browser-result').textContent = 'PASS';
})().catch(error => { button('browser-result').textContent = error.message; });
</script>
</body>"""), encoding="utf-8")
        result = subprocess.run([chrome, "--headless", "--disable-background-networking", "--no-first-run",
                                 f"--user-data-dir={self.root / 'browser'}", "--dump-dom", fixture.as_uri()],
                                capture_output=True, text=True, timeout=30, check=True)
        browser_result = result.stdout.partition('<output id="browser-result">')[2].partition("</output>")[0]
        self.assertEqual(browser_result, "PASS", result.stderr)

    def test_bulk_suggestions_skip_saved_answers_and_drafts(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps([QUESTIONS[0]] * 7))
        port = self.start("/qd")
        self.assertEqual(self.post_answer(port, "Q1.1", "Wait for low tide")[0], 200)
        self.assertEqual(self.post_answer(port, "Q1.6", "Ask the harbourmaster")[0], 200)
        for part, state in (("Q1.5", "answered"), ("Q1.6", "open"), ("Q1.7", "closed")):
            self.assertEqual(self.run_cli("mark", part, state)[0], 0)
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

    def test_partial_batch_displays_saved_answer_and_keeps_unsent_drafts(self):
        self.add("questions", "Tide decisions", "Decide the launch plan.", "--body-file", "-",
                 stdin=json.dumps(QUESTIONS))
        self.check_answer_page(self.start("/qd"), """
typeAnswer(1, 'Hold <for the pilot>');
typeAnswer(2, 'Blue');
const sent = [];
window.fetch = async (url, options) => {
  sent.push(JSON.parse(options.body));
  if (sent.length === 1) {
    return {ok: true, json: async () => ({
      answer: 'Hold <for the pilot>', revision: 'saved-revision',
      answered_at: '2026-01-12T15:00:00Z', delivery: 'sent'
    })};
  }
  return {ok: false, text: async () => 'Question changed. Reload before sending.'};
};
button('send').click();
await new Promise(resolve => setTimeout(resolve, 0));
check(sent.map(answer => answer.part).join(',') === 'Q1.1,Q1.2', 'The batch did not send both parts');
check(card(1).querySelector('.badge').textContent === 'Answered', 'The saved badge was stale');
const saved = card(1).querySelector('.saved-answer');
check(!saved.hidden && saved.querySelector('.record').textContent === 'Hold <for the pilot>',
      'The saved answer was missing or interpreted as HTML');
check(saved.querySelector('.delivery').textContent === 'Sent', 'The delivery label was stale');
check(saved.querySelector('time').dateTime === '2026-01-12T15:00:00Z', 'The saved timestamp was stale');
check(document.querySelector('.progress').textContent === '1 of 2 open', 'Progress was stale');
check(field(2).value === 'Blue', 'The unsent draft was lost');
check(button('send').textContent === 'Send 1 answer', 'The saved part was still queued');
check(document.querySelector('[role="status"]').textContent.includes('Question changed.'), 'The conflict was hidden');
""")

    def test_binding_uses_only_an_explicit_or_single_home(self):
        homes = [{"path": "/orchard"}, {"path": "/garden"}]
        self.assertIsNone(quarterdeck.answer_home({"homes": homes}))
        self.assertEqual(Path("/orchard"), quarterdeck.answer_home({"homes": homes[:1]}))
        self.assertEqual(Path("/grove"), quarterdeck.answer_home({"homes": homes, "firstmate_home": "/grove"}))

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

    def test_legacy_service_flags_and_config_keep_the_page_read_only(self) -> None:
        self.add_all_kinds()
        home = self.root / "firstmate"
        home.mkdir()
        (home / "backlog.md").write_text("Legacy work item")
        config = self.root / "legacy.json"
        config.write_text(json.dumps({"homes": [{"path": str(home)}], "captain_waits": {}}))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with subprocess.Popen(
            [sys.executable, str(ROOT / "quarterdeck.py"), "serve", "--port", str(port), "--config", str(config),
             "--allow-marks", "--watch-waits", "--no-snapshot", "--home", str(home)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as process:
            try:
                self.assertTrue(select.select([process.stdout], [], [], 10)[0], "server did not start")
                self.assertIn("Serving Quarterdeck", process.stdout.readline())
                status, body, _headers = self.fetch(port, "/")
                self.assertEqual(status, 200)
                self.assertIn("Tide launch decisions", body)
                self.assertNotIn("Legacy work item", body)
                self.assertNotIn(str(home), body)
                before = quarterdeck.store_path().read_bytes()
                self.assertEqual(self.fetch(port, "/", method="POST")[0], 405)
                self.assertEqual(quarterdeck.store_path().read_bytes(), before)
            finally:
                process.terminate()
                process.communicate(timeout=10)

    def test_damaged_store_returns_an_error_without_server_paths(self) -> None:
        self.add_all_kinds()
        path = quarterdeck.store_path()
        damaged = self.store()
        del damaged["items"][0]["body"]
        path.write_text(json.dumps(damaged))
        port = self.start()
        for route in ("/", "/q2.html"):
            status, body, _headers = self.fetch(port, route)
            self.assertEqual(status, 500)
            self.assertIn("could not read its item store", body)
            self.assertNotIn(str(path), body)
        self.assertEqual(self.fetch(port, "/Q1")[0], 404)


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
