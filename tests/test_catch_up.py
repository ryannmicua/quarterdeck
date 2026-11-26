from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_quarterdeck as base  # noqa: E402

quarterdeck = base.quarterdeck
StoreCase = base.StoreCase


class CatchUpCase(StoreCase):
    def notice(self, title="Tide review is ready", summary="The review is published and saved.", *extra):
        code, out, err = self.run_cli("catch-up", "add", "--title", title, "--summary", summary, *extra)
        self.assertEqual(code, 0, err)
        return out.strip()

    def start(self, *, ask_home: Path | None = None) -> int:
        handler = quarterdeck.make_handler("Quarterdeck", "/qd", ask_home=ask_home)
        server = quarterdeck.make_server("127.0.0.1", 0, handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def get(self, port: int, path: str) -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.read().decode("utf-8")

    def post_read(self, port: int, payload, origin: str | None = None):
        base = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(base + "/qd/catch-ups/read", method="POST", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "Origin": origin or base})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def age_read(self, notice_id: str, days: int) -> None:
        data = self.store()
        for notice in data["catch_ups"]:
            if notice["id"] == notice_id:
                moment = datetime.now(timezone.utc) - timedelta(days=days)
                notice["read_at"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        quarterdeck.store_path().write_text(json.dumps(data), encoding="utf-8")


class CatchUpCommandTests(CatchUpCase):
    def test_add_prints_ids_that_are_never_reused(self) -> None:
        self.assertEqual(self.notice(), "C1")
        self.assertEqual(self.notice("Second"), "C2")
        self.assertEqual(self.run_cli("catch-up", "read", "--all")[0], 0)
        self.age_read("C1", 8)
        self.age_read("C2", 8)
        self.assertEqual(self.run_cli("catch-up", "read", "C2")[0], 0)
        self.assertEqual(self.store()["catch_ups"], [])
        self.assertEqual(self.notice("Third"), "C3")
        self.assertEqual(self.store()["next_catch_up"], 4)

    def test_a_notice_links_to_an_item_a_page_and_a_project(self) -> None:
        item = self.add("resource", "Tide chart", "A chart.", "--url", "https://example.com/c", "--link-text", "Chart")
        self.notice("Linked", "All three links are set.", "--item", item.lower(), "--url", "https://example.com/pr/1",
                    "--link-text", "Open the change", "--project", "harbour")
        saved = self.store()["catch_ups"][0]
        self.assertEqual((saved["item"], saved["url"], saved["link_text"], saved["project"]),
                         ("Q1", "https://example.com/pr/1", "Open the change", "harbour"))
        self.assertNotIn("read_at", saved)
        code, output, error = self.run_cli("catch-up", "read", "C1")
        self.assertEqual(code, 0, error)
        self.assertIn("Linked\n  All three links are set.\n", output)
        for detail in ("Project: harbour", "Item: Q1", "Open the change: https://example.com/pr/1"):
            self.assertLess(output.index(detail), output.index("C1 Read"))
        self.assertIn("read_at", self.store()["catch_ups"][0])

    def test_invalid_notices_are_refused_and_nothing_is_saved(self) -> None:
        bad = [("--url", "https://example.com"), ("--link-text", "Open"), ("--item", "Q9"), ("--item", "nope"),
               ("--url", "javascript:alert(1)", "--link-text", "Open"), ("--project", "bad/../x/y")]
        for extra in bad:
            code, _, err = self.run_cli("catch-up", "add", "--title", "T", "--summary", "S", *extra)
            self.assertEqual(code, 2, extra)
            self.assertTrue(err.startswith("quarterdeck: "), err)
        self.assertEqual(self.run_cli("catch-up", "add", "--title", "T", "--summary", "x" * 301)[0], 2)
        self.assertNotIn("catch_ups", self.store() if quarterdeck.store_path().exists() else {})

    def test_list_shows_unread_notices_newest_first(self) -> None:
        self.assertEqual(self.run_cli("catch-up", "list")[1], "No unread catch-up notices.\n")
        self.notice("First")
        self.notice("Second")
        self.run_cli("catch-up", "read", "c1")
        self.notice("Third")
        lines = self.run_cli("catch-up", "list")[1].splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["C3", "C2"])

    def test_read_records_the_time_once_and_all_marks_every_unread_notice(self) -> None:
        for title in ("A", "B", "C"):
            self.notice(title)
        output = self.run_cli("catch-up", "read", "C1")[1]
        self.assertIn("A\n  The review is published and saved.\n", output)
        self.assertTrue(output.endswith("C1 Read\n"))
        first = self.store()["catch_ups"][0]["read_at"]
        self.assertRegex(first, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.age_read("C1", 1)
        stamp = self.store()["catch_ups"][0]["read_at"]
        self.run_cli("catch-up", "read", "C1")
        self.assertEqual(self.store()["catch_ups"][0]["read_at"], stamp)
        output = self.run_cli("catch-up", "read", "--all")[1]
        self.assertLess(output.index("  C\n"), output.index("  B\n"))
        self.assertTrue(output.endswith("C3 Read\nC2 Read\n"))
        self.assertEqual(self.run_cli("catch-up", "read", "--all")[1], "No unread catch-up notices.\n")
        for argv in (("read",), ("read", "C9"), ("read", "C1", "--all"), ("read", "bad")):
            self.assertEqual(self.run_cli("catch-up", *argv)[0], 2, argv)

    def test_read_all_allows_a_writer_during_output_and_marks_only_printed_notices(self) -> None:
        self.notice("Original notice")
        test = self

        class Output(io.StringIO):
            def flush(self):
                test.assertIn("Original notice", self.getvalue())
                test.assertNotIn("read_at", test.store()["catch_ups"][0])
                writer = subprocess.run(
                    [sys.executable, str(base.ROOT / "quarterdeck.py"), "catch-up", "add",
                     "--title", "New notice", "--summary", "Saved while output was being flushed."],
                    capture_output=True, text=True, timeout=5,
                )
                test.assertEqual((writer.returncode, writer.stdout.strip()), (0, "C2"), writer.stderr)

        output = Output()
        with redirect_stdout(output):
            self.assertEqual(quarterdeck.main(["catch-up", "read", "--all"]), 0)
        self.assertTrue(output.getvalue().endswith("C1 Read\n"))
        original, new = self.store()["catch_ups"]
        self.assertIn("read_at", original)
        self.assertNotIn("read_at", new)

    def test_default_retention_prunes_read_notices_after_seven_days(self) -> None:
        for title in ("A", "B", "C"):
            self.notice(title)
        self.run_cli("catch-up", "read", "C1", "C2")
        self.age_read("C1", 8)
        self.age_read("C2", 6)
        self.notice("D")
        self.assertEqual([n["id"] for n in self.store()["catch_ups"]], ["C2", "C3", "C4"])

    def test_cli_add_and_read_use_configured_retention(self) -> None:
        config = self.root / "config" / "quarterdeck.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"catch_up_days": 2}), encoding="utf-8")
        self.notice("A")
        self.run_cli("catch-up", "read", "C1")
        self.age_read("C1", 3)
        self.notice("B")
        self.assertEqual([n["id"] for n in self.store()["catch_ups"]], ["C2"])
        self.notice("C")
        self.run_cli("catch-up", "read", "C2")
        self.age_read("C2", 3)
        self.assertEqual(self.run_cli("catch-up", "read", "--all")[0], 0)
        self.assertEqual([n["id"] for n in self.store()["catch_ups"]], ["C3"])
        for command in (("add", "--title", "D", "--summary", "S"), ("read", "C3")):
            with self.assertRaises(SystemExit) as rejected:
                self.run_cli("catch-up", *command, "--config", str(config))
            self.assertEqual(rejected.exception.code, 2)

    def test_cli_rejects_invalid_retention(self) -> None:
        config = self.root / "config" / "quarterdeck.json"
        config.parent.mkdir()
        self.notice()
        before = self.store()
        for bad in (0, -1, "7", 3651, True, 2.5):
            config.write_text(json.dumps({"catch_up_days": bad}), encoding="utf-8")
            code, _, error = self.run_cli("catch-up", "read", "C1")
            self.assertEqual(code, 2, bad)
            self.assertIn("config catch_up_days", error)
            self.assertEqual(self.store(), before)

    def test_a_damaged_notice_is_refused_when_the_store_is_read(self) -> None:
        self.notice()
        for change in ({"title": ""}, {"saved_at": "yesterday"}, {"id": "Q1"}, {"extra": 1}, {"url": "https://example.com"},
                       {"read_at": "soon"}, {"item": "7"}):
            data = self.store()
            data["catch_ups"][0].update(change)
            quarterdeck.store_path().write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(self.run_cli("catch-up", "list")[0], 2, change)
            self.assertEqual(self.run_cli("list")[0], 2, change)
            data["catch_ups"][0] = {"id": "C1", "title": "Tide review is ready", "summary": "The review is published and saved.",
                                    "saved_at": "2026-11-25T01:00:00Z"}
            quarterdeck.store_path().write_text(json.dumps(data), encoding="utf-8")

    def test_old_stores_without_notices_still_load_and_write_no_notice_keys(self) -> None:
        self.add_all_kinds()
        self.assertEqual(self.run_cli("catch-up", "list")[1], "No unread catch-up notices.\n")
        self.assertNotIn("catch_ups", self.store())

    def test_help_names_the_command_and_its_docs(self) -> None:
        code, out, _ = self.run_cli("help", "catch-up")
        self.assertEqual(code, 0)
        self.assertIn("catch_up_days", out)
        self.assertIn("how-to/catch-up.md", out)


class CatchUpPageTests(CatchUpCase):
    def test_serve_help_describes_notice_access(self) -> None:
        code, out, error = self.run_cli("help", "serve")
        self.assertEqual(code, 0, error)
        help_text = " ".join(out.split())
        self.assertIn("mark Catch-up notices read by ID as the owner", help_text)

    def test_needs_you_shows_and_acknowledges_notices_with_the_open_questions(self) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the Catch-up browser test")
        item = self.add("questions", "Tide launch decisions", "One question.", "--body-file", "-", stdin=json.dumps([
            {"question": "Merge now?", "context": "Checks are green.", "options": ["Merge", "Hold"]}]))
        self.notice("Quay review", "The work is published.", "--item", item, "--project", "harbour",
                    "--url", "https://example.com/review", "--link-text", "Open the review")
        self.notice("Second notice")
        self.notice("Third notice")
        steps = """
const tab = document.getElementById('desk-tab-needs-you');
const count = tab.querySelector('.tab-count');
const panel = document.getElementById('desk-panel-needs-you');
const question = panel.querySelector('a[href="q1.html#Q1.1"]');
const notice = panel.querySelector('button[data-read="C1"]').closest('tr');
const waitForCount = expected => new Promise(resolve => {
  const observer = new MutationObserver(() => {
    if (count.textContent === expected) { observer.disconnect(); resolve(); }
  });
  observer.observe(count, {childList:true});
});
check(panel.hidden && count.textContent === '4', 'Catch-up did not contribute one row per notice');
tab.click();
check(notice.textContent.includes('Quay review') && notice.textContent.includes('The work is published.'),
      'The notice missed its title or summary');
check(innerWidth <= 640 && notice.querySelector('.notice-project').checkVisibility() &&
      notice.querySelector('.notice-project').textContent === 'harbour', 'The phone layout hid the notice project');
check(notice.querySelector('a[href="q1.html"]').textContent === 'Tide launch decisions' &&
      notice.querySelector('a[href="https://example.com/review"]').textContent === 'Open the review',
      'The notice links missed their destinations');
let changed = waitForCount('3');
notice.querySelector('button').click();
await changed;
check(!notice.isConnected && question.isConnected, 'Mark read removed the wrong row');
changed = waitForCount('1');
panel.querySelector('[data-read-all]').click();
await changed;
check(!panel.querySelector('.notice') && question.isConnected, 'Bulk acknowledgment changed the question');
"""
        page = base.WorkersTests.instrumented_page(quarterdeck.list_page, steps)
        with patch.object(quarterdeck, "list_page", page):
            outcome, stderr = base.WorkersTests.browser_page(self, self.start(), "/qd/", window_size="560,900")
        self.assertEqual(outcome, "PASS", stderr)
        self.assertTrue(all("read_at" in notice for notice in self.store()["catch_ups"]))

    def test_stale_and_incomplete_notice_batches_leave_the_rows_available(self) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the Catch-up browser test")
        self.notice("Expired notice")
        self.notice("Still unread")
        snapshot = self.store()
        self.assertEqual(self.run_cli("catch-up", "read", "C1")[0], 0)
        self.age_read("C1", 8)
        self.notice("New arrival")
        steps = """
const tab = document.getElementById('desk-tab-needs-you');
tab.click();
const panel = document.getElementById('desk-panel-needs-you');
const count = tab.querySelector('.tab-count');
const bulk = panel.querySelector('[data-read-all]');
const status = document.getElementById('desk-status');
const current = panel.querySelector('[data-read="C2"]');
const stale = panel.querySelector('[data-read="C1"]');
const clickAndWait = button => new Promise(resolve => {
  const observer = new MutationObserver(() => {
    if (!button.disabled) { observer.disconnect(); resolve(); }
  });
  observer.observe(button, {attributes:true, attributeFilter:['disabled']});
  button.click();
});
const realFetch = window.fetch;
window.fetch = async () => Response.json({read:['C2']});
await clickAndWait(bulk);
check(status.textContent.includes('Try again') && count.textContent === '2' &&
      current.isConnected && stale.isConnected, 'Incomplete success removed a notice or changed the count');
window.fetch = realFetch;
await clickAndWait(bulk);
check(status.textContent.includes('Reload') && count.textContent === '2' &&
      current.isConnected && stale.isConnected && !current.disabled && !stale.disabled,
      'A stale batch removed or disabled a notice');
await clickAndWait(current);
check(!current.isConnected && stale.isConnected && count.textContent === '1' && bulk.hidden,
      'The unread notice was no longer individually actionable after a conflict');
"""
        list_page = quarterdeck.list_page

        def stale_page(data, *args, **kwargs):
            return list_page(snapshot, *args, **kwargs)

        page = base.WorkersTests.instrumented_page(stale_page, steps)
        with patch.object(quarterdeck, "list_page", page):
            outcome, stderr = base.WorkersTests.browser_page(self, self.start(), "/qd/")
        self.assertEqual(outcome, "PASS", stderr)
        current, new = self.store()["catch_ups"]
        self.assertIn("read_at", current)
        self.assertNotIn("read_at", new)

    def test_final_notice_with_deleted_item_keeps_its_external_link_until_read(self) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the Catch-up browser test")
        item = self.add("resource", "Old chart", "A chart.", "--url", "https://example.com/c", "--link-text", "Chart")
        self.notice("<b>Review</b>", "Tom & <i>Jerry</i>", "--item", item,
                    "--url", "https://example.com/review", "--link-text", "Open review")
        self.assertEqual(self.run_cli("remove", item)[0], 0)
        steps = """
const tab = document.getElementById('desk-tab-needs-you');
tab.click();
const panel = document.getElementById('desk-panel-needs-you');
const count = tab.querySelector('.tab-count');
const notice = panel.querySelector('tr.notice');
const guidance = panel.querySelector('.empty');
const table = panel.querySelector('.desk');
check(!notice.querySelector('a[href="q1.html"]') &&
      notice.querySelector('a[href="https://example.com/review"]').textContent === 'Open review',
      'Deleting the linked item broke the notice links');
check(notice.textContent.includes('<b>Review</b>') && notice.textContent.includes('Tom & <i>Jerry</i>') &&
      !notice.querySelector('b, i'), 'Notice markup was interpreted instead of shown as text');
check(count.textContent === '1' && !table.hidden && guidance.hidden, 'The notice must hide empty guidance');
const read = notice.querySelector('button');
await new Promise(resolve => {
  const observer = new MutationObserver(() => {
    if (!read.disabled) { observer.disconnect(); resolve(); }
  });
  observer.observe(read, {attributes:true, attributeFilter:['disabled']});
  read.focus();
  read.click();
});
check(!notice.isConnected && count.hidden && table.hidden && guidance.checkVisibility(),
      'Acknowledging the final notice did not reveal the empty state');
check(document.activeElement === tab, 'Removing the focused notice did not restore keyboard focus');
"""
        page = base.WorkersTests.instrumented_page(quarterdeck.list_page, steps)
        with patch.object(quarterdeck, "list_page", page):
            outcome, stderr = base.WorkersTests.browser_page(self, self.start(), "/qd/")
        self.assertEqual(outcome, "PASS", stderr)
        self.assertIn("read_at", self.store()["catch_ups"][0])

    def test_the_served_desk_with_notices_cannot_be_framed(self) -> None:
        self.notice()
        port = self.start()
        for path in ("/", "/index.html", "/qd", "/qd/", "/qd/index.html"):
            with self.subTest(path=path):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                    self.assertEqual(response.headers.get("Content-Security-Policy"), "frame-ancestors 'none'")

    def test_rendered_pages_exclude_catch_up(self) -> None:
        self.notice("Private notice", "Private summary", "--url", "https://example.com/review",
                    "--link-text", "Private review link")
        out = self.root / "out"
        for name in ("index.html", "snapshot.html"):
            output = out / name
            self.assertEqual(self.run_cli("render", "--output", str(output))[0], 0)
            page = output.read_text(encoding="utf-8")
            for hidden in ("Catch-up", "Private notice", "Private summary", "Private review link",
                           "https://example.com/review", "<button"):
                self.assertNotIn(hidden, page)

    def test_marking_read_uses_the_owner_path_and_records_the_time(self) -> None:
        for title in ("A", "B", "C"):
            self.notice(title)
        port = self.start()
        self.assertEqual(self.post_read(port, {"ids": ["C1"]}, origin="https://example.org")[0], 403)
        for bad in ({"ids": []}, {"ids": ["Q1"]}, {"ids": ["c1"]}, {"ids": ["C1", "c3"]}, {"ids": "C1"},
                    {"ids": ["C1"], "all": True}, {}, []):
            self.assertEqual(self.post_read(port, bad)[0], 400, bad)
        before = self.store()
        self.assertEqual(self.post_read(port, {"ids": ["C1", "C1"]})[0], 400)
        self.assertEqual(self.store(), before)
        self.assertNotIn("read_at", self.store()["catch_ups"][0])
        status, record = self.post_read(port, {"ids": ["C3", "C1"]})
        self.assertEqual(status, 200)
        self.assertEqual(record, {"read": ["C3", "C1"]})
        read_at = self.store()["catch_ups"][0]["read_at"]
        self.assertEqual(self.post_read(port, {"ids": ["C1"]}), (200, {"read": ["C1"]}))
        self.assertEqual(self.store()["catch_ups"][0]["read_at"], read_at)
        unread = self.run_cli("catch-up", "list")[1].splitlines()
        self.assertEqual(len(unread), 1)
        self.assertTrue(unread[0].startswith("C2 "))
        self.assertEqual(self.post_read(port, {"ids": ["C2"]})[0], 200)
        self.assertEqual(self.run_cli("catch-up", "list")[1], "No unread catch-up notices.\n")

    def test_served_acknowledgment_prunes_using_configured_retention(self) -> None:
        config = self.root / "config" / "quarterdeck.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"catch_up_days": 30, "base_path": "/qd"}), encoding="utf-8")
        for title in ("A", "B", "C", "D"):
            self.notice(title)
        self.assertEqual(self.run_cli("catch-up", "read", "C1", "C2")[0], 0)
        self.age_read("C1", 31)
        self.age_read("C2", 8)
        data = self.store()
        data["catch_ups"][2]["saved_at"] = "2020-01-01T00:00:00Z"
        quarterdeck.store_path().write_text(json.dumps(data), encoding="utf-8")
        with self.serve_cli() as port:
            self.assertEqual(self.post_read(port, {"ids": ["C4"]}), (200, {"read": ["C4"]}))
        notices = self.store()["catch_ups"]
        self.assertEqual([notice["id"] for notice in notices], ["C2", "C3", "C4"])
        self.assertNotIn("read_at", notices[1])
