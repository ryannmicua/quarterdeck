from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
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
        self.assertEqual(self.run_cli("catch-up", "read", "C1")[1], "C1 Read\n")
        first = self.store()["catch_ups"][0]["read_at"]
        self.assertRegex(first, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.age_read("C1", 1)
        stamp = self.store()["catch_ups"][0]["read_at"]
        self.run_cli("catch-up", "read", "C1")
        self.assertEqual(self.store()["catch_ups"][0]["read_at"], stamp)
        self.assertEqual(self.run_cli("catch-up", "read", "--all")[1], "C3 Read\nC2 Read\n")
        self.assertEqual(self.run_cli("catch-up", "read", "--all")[1], "No unread catch-up notices.\n")
        for argv in (("read",), ("read", "C9"), ("read", "C1", "--all"), ("read", "bad")):
            self.assertEqual(self.run_cli("catch-up", *argv)[0], 2, argv)

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
        self.assertIn("unread Catch-up notice titles, summaries, links and projects", help_text)
        self.assertIn("mark Catch-up notices read as the owner", help_text)

    def test_unread_notices_show_above_the_questions_newest_first(self) -> None:
        item = self.add("questions", "Tide launch decisions", "Two questions.", "--body-file", "-", stdin=json.dumps([
            {"question": "Merge now?", "context": "Checks are green.", "options": ["Merge", "Hold"]}]))
        self.notice("Older notice", "The older one.")
        self.notice("Newer notice", "The newer one.", "--item", item, "--url", "https://example.com/review", "--link-text", "Open the review")
        page = self.get(self.start(), "/qd/")
        self.assertLess(page.index('class="catch-up"'), page.index('class="desk"'))
        self.assertLess(page.index("Newer notice"), page.index("Older notice"))
        self.assertIn("<h2>Catch-up <span data-count>(2)</span></h2>", page)
        self.assertIn("<button type=\"button\" data-read-all>Mark all read</button>", page)
        self.assertIn('data-read="C2" aria-label="Mark read, Newer notice"', page)
        self.assertIn('<a href="q1.html">Tide launch decisions</a>', page)
        self.assertIn('<a href="https://example.com/review" rel="noopener noreferrer">Open the review</a>', page)
        self.assertRegex(page, r'<time class="day" datetime="\d{4}-\d\d-\d\dT[\d:]+Z">\d{4}-\d\d-\d\d</time>')

    def test_one_notice_has_no_count_and_no_mark_all(self) -> None:
        self.notice()
        page = self.get(self.start(), "/qd/")
        self.assertIn("<h2>Catch-up</h2>", page)
        self.assertIn("No questions need your attention.", page)
        self.assertNotIn("data-read-all>", page)
        self.assertNotIn("(1)", page)

    def test_the_section_is_absent_when_nothing_is_unread_or_saved(self) -> None:
        port = self.start()
        self.assertNotIn("Catch-up", self.get(port, "/qd/"))
        self.notice()
        self.run_cli("catch-up", "read", "C1")
        page = self.get(port, "/qd/")
        self.assertNotIn("Catch-up", page)

    def test_the_served_desk_with_notices_cannot_be_framed(self) -> None:
        self.notice()
        port = self.start()
        for path in ("/", "/index.html", "/qd", "/qd/", "/qd/index.html"):
            with self.subTest(path=path):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                    self.assertEqual(response.headers.get("Content-Security-Policy"), "frame-ancestors 'none'")

    def test_a_removed_item_leaves_a_notice_without_its_link_and_text_is_escaped(self) -> None:
        self.add("resource", "Gone", "Soon removed.", "--url", "https://example.com", "--link-text", "Open")
        self.notice("<b>Bold</b> & more", "Tom & <i>Jerry</i>", "--item", "Q1")
        self.run_cli("remove", "Q1")
        page = self.get(self.start(), "/qd/")
        self.assertNotIn('href="q1.html"', page)
        self.assertIn("&lt;b&gt;Bold&lt;/b&gt; &amp; more", page)
        self.assertNotIn("<i>Jerry</i>", page)

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
        self.assertNotIn("read_at", self.store()["catch_ups"][0])
        status, record = self.post_read(port, {"ids": ["C1", "C3"]})
        self.assertEqual(status, 200)
        self.assertEqual(record, {"read": ["C1", "C3"]})
        read_at = self.store()["catch_ups"][0]["read_at"]
        self.assertEqual(self.post_read(port, {"ids": ["C1"]}), (200, {"read": ["C1"]}))
        self.assertEqual(self.store()["catch_ups"][0]["read_at"], read_at)
        page = self.get(port, "/qd/")
        self.assertIn("B", re.findall(r"<h3>([^<]+)</h3>", page))
        self.assertNotIn("(1)", page)
        self.assertEqual(self.post_read(port, {"ids": ["C2"]})[0], 200)
        self.assertNotIn("Catch-up", self.get(port, "/qd/"))

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

    def test_acknowledgment_buttons_in_the_browser(self) -> None:
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the Catch-up browser test")
        self.notice("A")
        self.notice("B")
        self.notice("C")
        stale_store = self.store()
        stale_store["catch_ups"][1]["saved_at"] = "2026-11-25T18:30:00Z"
        quarterdeck.store_path().write_text(json.dumps(stale_store), encoding="utf-8")
        self.run_cli("catch-up", "read", "C1")
        self.age_read("C1", 8)
        self.notice("D")
        self.run_cli("catch-up", "read", "C4")
        reference = self.add("resource", "Tide chart", "A chart.", "--url", "https://example.com/chart",
                             "--link-text", "Chart")
        data = self.store()
        data["catch_ups"][0]["item"] = reference
        quarterdeck.store_path().write_text(json.dumps(data), encoding="utf-8")
        home = self.root / "firstmate"
        home.mkdir()
        port = self.start(ask_home=home)
        status, _ = self.post_read(port, {"ids": ["C1", "C2"]})
        self.assertEqual(status, 409)
        self.assertNotIn("read_at", self.store()["catch_ups"][0])
        list_page = quarterdeck.list_page
        catch_up_section = quarterdeck.catch_up_section
        pages = iter([stale_store])

        def instrumented_page(data, *args, **kwargs):
            snapshot = next(pages, data)
            kwargs["notices"] = catch_up_section(snapshot)
            return list_page(snapshot, *args, **kwargs).replace("</body>", """
<output id="browser-result">Incomplete</output>
<script>
{
  const result = document.getElementById('browser-result');
  const control = id => document.querySelector('[data-read="' + id + '"]');
  const noticeDay = () => document.querySelector('.note[data-id="C2"] time').textContent;
  const bulk = document.querySelector('[data-read-all]');
  const status = document.querySelector('[data-catch-up-status]');
  const reloadMessage = 'A notice may already be read. Reload the page when ready.';
  const retryMessage = 'The notice could not be marked read. Try again.';
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const clickAndWait = button => new Promise(resolve => {
    const observer = new MutationObserver(() => {
      if (!button.disabled) {
        observer.disconnect();
        resolve();
      }
    });
    observer.observe(button, {attributes:true, attributeFilter:['disabled']});
    button.click();
  });
  (async () => {
    check(control('C1'), 'Conflict recovery should preserve the original Desk page');
    check(status.getAttribute('role') === 'status' && status.getClientRects().length &&
          status.getBoundingClientRect().height <= 1 && getComputedStyle(status).visibility === 'visible',
          'The empty live region should be available without reserving visible space');
    check(noticeDay() === 'Nov 26, 2026', 'The initial notice should show the next date in Manila');
    const form = document.getElementById('talk-form');
    const draft = document.getElementById('talk-message');
    const guidance = document.querySelector('main > p.empty');
    document.getElementById('open-talk').click();
    draft.value = 'Draft about the tide chart.';
    draft.dispatchEvent(new Event('input', {bubbles:true}));
    const fetchFromDesk = window.fetch;
    let failSave;
    window.fetch = () => new Promise(resolve => { failSave = resolve; });
    const failed = clickAndWait(control('C3'));
    check(control('C1').disabled && control('C2').disabled && control('C3').disabled && bulk.disabled,
          'Every acknowledgment control should be disabled during a save');
    failSave(new Response('', {status:503}));
    await failed;
    check(control('C3') && !bulk.disabled && status.textContent === retryMessage,
          'A failed save should keep the notice, restore the controls, and offer retry');
    check(status.getBoundingClientRect().height > 1, 'The failure message should be visible');
    window.fetch = async () => new Response('<h1>Sign in</h1>', {headers:{'Content-Type':'text/html'}});
    await clickAndWait(control('C3'));
    check(control('C3') && status.textContent === retryMessage,
          'An HTTP 200 sign-in page should leave the notice visible');
    window.fetch = async () => Response.json({read:['C3']});
    await clickAndWait(bulk);
    check(control('C1') && control('C2') && control('C3') && status.textContent === retryMessage,
          'An incomplete batch acknowledgment should leave all rows visible');
    let releaseSave;
    window.fetch = (...args) => new Promise(resolve => {
      releaseSave = () => resolve(fetchFromDesk(...args));
    });
    control('C3').focus();
    const saved = clickAndWait(control('C3'));
    draft.focus();
    releaseSave();
    await saved;
    window.fetch = fetchFromDesk;
    check(!control('C3') && document.querySelector('[data-count]').textContent === '(2)',
          'A successful save should remove the row and update the count');
    check(document.activeElement === draft, 'A delayed acknowledgment should leave focus in the draft');
    await clickAndWait(bulk);
    await clickAndWait(control('C1'));
    check(control('C1') && control('C2') && !control('C3') && !bulk.disabled &&
          document.querySelector('[data-count]').textContent === '(2)' && status.textContent === reloadMessage,
          'A stale batch or row should stay visible with guidance to reload');
    check(noticeDay() === 'Nov 26, 2026', 'The notice should keep its local date');
    check(!document.querySelector('.catch-up a[href="q1.html"]') &&
          document.querySelector('main > p.empty') === guidance &&
          guidance.textContent === 'No questions need your attention.',
          'A conflict should preserve the displayed Desk snapshot');
    check(document.getElementById('talk-form') === form && !form.hidden &&
          document.getElementById('talk-message') === draft && draft.value === 'Draft about the tide chart.',
          'A conflict should preserve the open Talk to Firstmate form and its draft');
    control('C2').focus();
    await clickAndWait(control('C2'));
    check(control('C1') && !control('C2') && !document.querySelector('[data-read-all]') &&
          !document.querySelector('[data-count]') && !status.textContent,
          'An unread notice should still be actionable after a conflict');
    check(document.activeElement === document.querySelector('.catch-up h2'),
          'Removing the focused acknowledgment control should focus the section heading');
    result.textContent = 'PASS';
  })().catch(error => { result.textContent = error.message; });
}
</script>
</body>""")

        with patch.object(quarterdeck, "list_page", instrumented_page):
            browser = subprocess.run(
                [chrome, "--headless", "--disable-background-networking", "--no-first-run", "--lang=en-US",
                 f"--user-data-dir={self.root / 'browser'}", "--dump-dom", "--virtual-time-budget=10000",
                 f"http://127.0.0.1:{port}/qd/"],
                env={**os.environ, "TZ": "Asia/Manila", "LC_ALL": "en_US.UTF" "-8"},
                capture_output=True, text=True, timeout=30, check=True,
            )
        result = browser.stdout.partition('<output id="browser-result">')[2].partition("</output>")[0]
        self.assertEqual(result, "PASS", browser.stderr)
        self.assertTrue(all("read_at" in notice for notice in self.store()["catch_ups"]))


if __name__ == "__main__":
    unittest.main()
