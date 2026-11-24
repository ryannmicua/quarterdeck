from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

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
                     stdin="Buy rope\n- Paint the hull\n3. Check the sails\n"),
            self.add("questions", "Tide launch decisions", "Two open questions, saved to decide later.",
                     "--body-file", "-", stdin=json.dumps(QUESTIONS)),
            self.add("note", "Quay notes", "Thoughts on the quay.", "--body-file", "-", stdin="First part.\n\nSecond part.\n"),
        ]

    def store(self) -> dict:
        return json.loads(quarterdeck.store_path().read_text(encoding="utf-8"))


class ItemCommandTests(StoreCase):
    def test_add_prints_sequential_ids_and_stores_each_kind_by_value(self) -> None:
        body = self.root / "chores.txt"
        body.write_text("Buy rope\nPaint the hull\n", encoding="utf-8")
        ids = self.add_all_kinds()
        ids.append(self.add("list", "From a file", "A list read from a file.", "--body-file", str(body)))
        self.assertEqual(ids, ["Q1", "Q2", "Q3", "Q4", "Q5"])
        body.write_text("Changed later\n", encoding="utf-8")
        items = {item["id"]: item for item in self.store()["items"]}
        self.assertEqual(items["Q1"]["body"], {"url": "https://example.com/lavish/calls", "link_text": "Calls redesign (Lavish)"})
        self.assertEqual(items["Q2"]["body"]["entries"], ["Buy rope", "Paint the hull", "Check the sails"])
        self.assertEqual(items["Q3"]["body"]["questions"][1]["options"], ["Red", "Blue"])
        self.assertEqual(items["Q4"]["body"]["text"], "First part.\n\nSecond part.")
        self.assertEqual(items["Q5"]["body"]["entries"], ["Buy rope", "Paint the hull"], "the body is a copy, not a link")
        self.assertTrue(items["Q1"]["saved_at"].endswith("Z"))

    def test_removed_ids_are_never_reused(self) -> None:
        self.add_all_kinds()
        self.assertEqual(self.run_cli("remove", "Q4")[0], 0)
        self.assertEqual(self.run_cli("remove", "q3")[0], 0)
        self.assertEqual(self.add("note", "Later", "A later note.", "--body-file", "-", stdin="text"), "Q5")
        code, _out, err = self.run_cli("show", "Q4")
        self.assertEqual((code, err.strip()), (2, "quarterdeck: no item Q4"))

    def test_show_numbers_list_entries_and_questions(self) -> None:
        self.add_all_kinds()
        code, out, _err = self.run_cli("show", "Q3")
        self.assertEqual(code, 0)
        self.assertIn("Q3 · Tide launch decisions", out)
        self.assertIn("Q3.1  Merge the tide tables now?", out)
        self.assertIn("      Options: Merge | Hold", out)
        self.assertIn("      Suggestion: Merge", out)
        self.assertIn("Q3.2  Which colour for the quay?", out)
        self.assertIn("Q2.3  Check the sails", self.run_cli("show", "2")[1])
        shown = json.loads(self.run_cli("show", "Q1", "--json")[1])
        self.assertEqual(shown["title"], "Calls redesign")

    def test_done_leaves_the_main_list_and_reopen_brings_it_back(self) -> None:
        self.add_all_kinds()
        self.assertEqual(self.run_cli("done", "Q2")[0], 0)
        listed = self.run_cli("list")[1]
        self.assertNotIn("Harbor chores", listed)
        self.assertEqual(listed.splitlines()[0].split()[0], "Q4", "newest first")
        self.assertIn("Harbor chores  (done)", self.run_cli("list", "--all")[1])
        self.assertTrue(json.loads(self.run_cli("list", "--all", "--json")[1])[2]["done"])
        self.assertEqual(self.run_cli("reopen", "Q2")[0], 0)
        self.assertIn("Harbor chores", self.run_cli("list")[1])
        self.assertIsNone(self.store()["items"][1]["done_at"])

    def test_edit_changes_fields_and_keeps_kind_and_id(self) -> None:
        self.add_all_kinds()
        self.assertEqual(self.run_cli("edit", "Q1", "--title", "Calls page", "--url", "https://example.com/v2")[0], 0)
        self.assertEqual(self.run_cli("edit", "Q2", "--body-file", "-", stdin="Only this\n")[0], 0)
        items = {item["id"]: item for item in self.store()["items"]}
        self.assertEqual((items["Q1"]["title"], items["Q1"]["body"]["url"]), ("Calls page", "https://example.com/v2"))
        self.assertEqual(items["Q1"]["body"]["link_text"], "Calls redesign (Lavish)")
        self.assertEqual(items["Q2"]["body"]["entries"], ["Only this"])
        self.assertIn("edited_at", items["Q2"])
        for argv, message in (
            (("edit", "Q2", "--url", "https://example.com"), "only for a resource"),
            (("edit", "Q1", "--body-file", "-"), "not a body"),
            (("edit", "Q1",), "nothing to change"),
        ):
            with self.subTest(argv=argv):
                code, _out, err = self.run_cli(*argv, stdin="x")
                self.assertEqual(code, 2)
                self.assertIn(message, err)

    def test_invalid_items_are_refused_and_nothing_is_saved(self) -> None:
        cases = [
            (("--kind", "resource", "--title", "T", "--description", "D", "--url", "https://example.com"), "", "--link-text"),
            (("--kind", "resource", "--title", "T", "--description", "D", "--url", "javascript:alert(1)", "--link-text", "L"), "",
             "http:// or https://"),
            (("--kind", "list", "--title", "T", "--description", "D", "--body-file", "-"), "\n\n", "at least one entry"),
            (("--kind", "note", "--title", "T", "--description", "D"), "", "needs a body"),
            (("--kind", "note", "--title", " ", "--description", "D", "--body-file", "-"), "text", "title must not be empty"),
            (("--kind", "questions", "--title", "T", "--description", "D", "--body-file", "-"),
             json.dumps([{"question": "Q?", "options": ["A"]}]), "context"),
            (("--kind", "questions", "--title", "T", "--description", "D", "--body-file", "-"),
             json.dumps([{"question": "Q?", "context": "C"}]), "options or a suggestion"),
            (("--kind", "questions", "--title", "T", "--description", "D", "--body-file", "-"), "not json", "not valid JSON"),
            (("--kind", "note", "--title", "T", "--description", "D", "--url", "https://example.com", "--body-file", "-"), "x",
             "only for a resource"),
        ]
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
        path.write_text(json.dumps({"version": 1, "next_id": 2, "items": [{"id": "Q5", "kind": "note"}]}), encoding="utf-8")
        self.assertIn("above next_id", self.run_cli("list")[2])

    def test_ids_parse_in_any_case_with_or_without_the_q(self) -> None:
        self.assertEqual([quarterdeck.parse_id(value) for value in ("Q7", "q7", "7", " Q07 ")], ["Q7"] * 4)
        for value in ("Q0", "Q7.1", "seven", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quarterdeck.parse_id(value)

    def test_concurrent_adds_get_distinct_ids(self) -> None:
        results: list[str] = []

        def add() -> None:
            with quarterdeck.changing_store(quarterdeck.store_path()) as data:
                results.append(f"Q{data['next_id']}")
                data["next_id"] += 1

        threads = [threading.Thread(target=add) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results, key=lambda value: int(value[1:])), [f"Q{number}" for number in range(1, 9)])


class PageTests(StoreCase):
    def pages(self) -> dict[str, str]:
        return quarterdeck.site_pages(quarterdeck.read_store(quarterdeck.store_path()), "Quarterdeck")

    def test_empty_quarterdeck_shows_a_plain_empty_state(self) -> None:
        pages = self.pages()
        self.assertEqual(set(pages), {"index.html", "done.html"})
        self.assertIn("Nothing is saved yet.", pages["index.html"])
        self.assertNotIn("<table", pages["index.html"])

    def test_saved_page_lists_items_newest_first_with_their_fields(self) -> None:
        self.add_all_kinds()
        self.run_cli("done", "Q2")
        index = self.pages()["index.html"]
        rows = index.split("<tbody>", 1)[1]
        self.assertLess(rows.index(">Q4<"), rows.index(">Q3<"))
        self.assertLess(rows.index(">Q3<"), rows.index(">Q1<"))
        self.assertNotIn("Harbor chores", index, "done items leave the main view")
        self.assertIn('<a href="q3.html">Tide launch decisions</a>', index)
        self.assertIn("Two open questions, saved to decide later.", index)
        self.assertIn('<a href="done.html">1 done item</a>', index)
        for heading in ("ID", "Kind", "Title", "Description", "Saved"):
            self.assertIn(f'<th scope="col">{heading}</th>', index)
        self.assertIn("Harbor chores", self.pages()["done.html"])

    def test_item_pages_show_id_title_and_numbered_parts(self) -> None:
        self.add_all_kinds()
        pages = self.pages()
        self.assertIn('<p class="id">Q1</p><h1>Calls redesign</h1>', pages["q1.html"])
        self.assertIn('<a class="open-link" href="https://example.com/lavish/calls" rel="noopener noreferrer">'
                      "Calls redesign (Lavish)</a>", pages["q1.html"])
        self.assertIn('<li id="Q2.3"><span class="id">Q2.3</span><span>Check the sails</span></li>', pages["q2.html"])
        questions = pages["q3.html"]
        self.assertIn('<span class="id">Q3.1</span>', questions)
        self.assertIn("Merge the tide tables now?", questions)
        self.assertIn("Checks are green and the preview looks right.", questions)
        self.assertIn("<li>Hold</li>", questions)
        self.assertIn('<span class="label">Suggestion</span><br>Merge', questions)
        self.assertIn("<p>First part.</p><p>Second part.</p>", pages["q4.html"])

    def test_text_is_escaped_and_no_server_path_appears(self) -> None:
        self.add("note", "<script>alert(1)</script>", "A & B", "--body-file", "-", stdin="<b>bold</b>")
        pages = self.pages()
        for page in pages.values():
            self.assertNotIn("<script>alert(1)</script>", page)
            self.assertNotIn(str(self.root), page)
            self.assertNotIn(str(Path.home()), page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", pages["q1.html"])


class RenderTests(StoreCase):
    def test_render_writes_every_page_and_retires_only_its_own_stale_pages(self) -> None:
        self.add_all_kinds()
        output = self.root / "site" / "index.html"
        output.parent.mkdir()
        (output.parent / "classic.html").write_text("<html><head>\n" + quarterdeck.GENERATED_MARKER + "\n</head></html>")
        (output.parent / "mine.html").write_text("<html>my own page</html>")
        code, out, err = self.run_cli("render", "--output", str(output))
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), str(output.resolve()))
        names = {path.name for path in output.parent.iterdir()}
        self.assertEqual(names, {"index.html", "done.html", "q1.html", "q2.html", "q3.html", "q4.html", "mine.html"})
        self.run_cli("remove", "Q4")
        self.run_cli("render", "--output", str(output))
        self.assertFalse((output.parent / "q4.html").exists(), "a removed item's page is retired")
        self.assertTrue((output.parent / "mine.html").exists(), "a file Quarterdeck did not write is kept")

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

    def test_pages_follow_the_store_without_a_restart(self) -> None:
        port = self.start()
        status, body, _headers = self.fetch(port, "/")
        self.assertEqual(status, 200)
        self.assertIn("Nothing is saved yet.", body)
        self.add_all_kinds()
        self.assertIn("Tide launch decisions", self.fetch(port, "/")[1])
        for path in ("/q3.html", "/Q3", "/q3"):
            with self.subTest(path=path):
                status, body, _headers = self.fetch(port, path)
                self.assertEqual(status, 200)
                self.assertIn("Q3.2", body)
        self.assertEqual(self.fetch(port, "/q9.html")[0], 404)
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

    def test_serve_accepts_the_flags_an_older_service_unit_passes(self) -> None:
        args = quarterdeck.make_parser().parse_args(
            ["serve", "--host", "127.0.0.1", "--port", "8765", "--allow-marks", "--watch-waits", "--no-snapshot", "--home", "/x"])
        self.assertTrue(args.allow_marks and args.watch_waits)
        self.assertNotIn("allow-marks", quarterdeck.make_parser()._subparsers._group_actions[0].choices["serve"].format_help())


class ServiceAndHelpTests(StoreCase):
    def test_service_unit_runs_serve_without_old_flags(self) -> None:
        args = quarterdeck.make_parser().parse_args(["service", "--host", "127.0.0.1", "--port", "9000", "--base-path", "/qd"])
        unit = quarterdeck.service_unit(args)
        self.assertIn('"serve" "--host" "127.0.0.1" "--port" "9000" "--base-path" "/qd"', unit)
        self.assertNotIn("allow-marks", unit)
        self.assertIn("Description=Quarterdeck read-only page of saved items", unit)

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
        for name in ("add", "list", "show", "edit", "remove", "done", "reopen", "render", "serve"):
            self.assertIn(name, names)
        for old in ("board", "reports", "topics", "waits", "attention", "export"):
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
        self.assertIn("No open items", result.stdout)


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
