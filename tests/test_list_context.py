from __future__ import annotations

import html
import json
import re
import shutil
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_quarterdeck as base  # noqa: E402

quarterdeck = base.quarterdeck
StoreCase = base.StoreCase

ONE_QUESTION = [{"question": "Merge now?", "context": "Checks are green.", "options": ["Merge", "Hold"]}]


class ListContextCase(StoreCase):
    """Detail pages opened from a list know that list, and nothing is kept on the server."""

    def start(self) -> int:
        handler = quarterdeck.make_handler("Quarterdeck", "/qd")
        server = quarterdeck.make_server("127.0.0.1", 0, handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def get(self, port: int, path: str) -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/qd/{path}", timeout=5) as response:
            return response.read().decode("utf-8")

    def questions(self, title: str, *extra: str) -> str:
        return self.add("questions", title, "One open question.", "--body-file", "-", *extra, stdin=json.dumps(ONE_QUESTION))

    def talk(self, title: str) -> str:
        code, out, err = self.run_cli("converse", "--title", title, "--text", f"Update on {title.lower()}.")
        self.assertEqual(code, 0, err)
        return out.strip()

    def answer(self, part: str) -> None:
        """Answer one question with a delivery that succeeds, so the set leaves Needs you."""
        home = self.root / "home"
        script = home / "bin" / "fm-inbox.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        quarterdeck.send_answer(quarterdeck.store_path(), home, {"part": part, "answer": "Merge", "revision": ""})

    def read_replies(self, *item_ids: str) -> None:
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            for item in data["items"]:
                if item["id"] in item_ids:
                    for thread in item["conversation"].values():
                        for message in thread:
                            message["read"] = True

    def strips(self, page: str) -> list[list[tuple[str, str]]]:
        """The links of each list strip on a page, as (address, words) pairs."""
        strips = re.findall(r'<nav class="list-nav[^"]*" aria-label="[^"]*">(.*?)</nav>', page)
        return [[(html.unescape(href), text) for href, text in re.findall(r'<a class="[a-z-]+" href="([^"]*)">(.*?)</a>', strip)]
                for strip in strips]

    def strip(self, port: int, path: str) -> list[tuple[str, str]]:
        """The list links of a page; the strip at the end of the page must say the same as the one at the start."""
        first, last = self.strips(self.get(port, path))
        self.assertEqual(first, last)
        return first


class ListContextAddressTests(ListContextCase):
    def test_an_address_names_a_list_or_nothing(self) -> None:
        parse = quarterdeck.parse_list_context
        self.assertEqual(parse("from=needs-you"), {"from": "needs-you"})
        self.assertEqual(parse("from=watch&project=Harbor"), {"from": "watch"})
        self.assertEqual(parse("from=answered&project=Harbor+lights&tag=research&search=Q3.1&other=1"),
                         {"from": "answered", "project": "Harbor lights", "tag": "research", "search": "Q3.1"})
        self.assertEqual(parse("from=answered&search=" + "x" * 500)["search"], "x" * 200)
        for query in ("", "from=", "from=desk", "from=Needs-you", "from=%00", "project=Harbor", "tab=needs-you", "from=a&" * 40):
            with self.subTest(query=query):
                self.assertIsNone(parse(query))

    def test_back_goes_to_the_tab_the_filters_and_the_row_of_the_list(self) -> None:
        back = quarterdeck.back_link_href
        self.assertEqual(back({"from": "needs-you"}, "Q3"), "index.html?tab=needs-you&row=Q3")
        self.assertEqual(back({"from": "archived"}, "Q12"), "index.html?tab=archived&row=Q12")
        self.assertEqual(back({"from": "watch"}, "Q3"), "watch.html#watch-Q3")
        self.assertEqual(back({"from": "answered", "project": "Harbor lights", "search": "pier & quay"}, "Q3"),
                         "index.html?hold=answered&project=Harbor+lights&search=pier+%26+quay&row=Q3")
        self.assertEqual(back({"from": "hold-conversations"}, "Q3"), "index.html?hold=conversations&row=Q3")


class BackAndNextTests(ListContextCase):
    def setUp(self) -> None:
        super().setUp()
        for title in ("Pier lights", "Quay signs", "Rope order"):
            self.questions(title)
        self.talk("Sail inventory")
        self.talk("Dock lights")
        self.port = self.start()

    def test_a_page_opened_from_needs_you_goes_back_to_needs_you_at_its_row(self) -> None:
        self.assertEqual(self.strip(self.port, "q2.html?from=needs-you")[0], ("index.html?tab=needs-you&row=Q2", "Back to Needs you"))

    def test_a_page_opened_from_each_list_names_that_list(self) -> None:
        for name, label, address in (
            ("conversations", "Conversations", "index.html?tab=conversations&row=Q4"),
            ("archived", "Archived", "index.html?tab=archived&row=Q4"),
            ("watch", "Watch", "watch.html#watch-Q4"),
            ("hold-conversations", "Hold conversations", "index.html?hold=conversations&row=Q4"),
            ("answered", "Answered", "index.html?hold=answered&row=Q4"),
            ("references", "References", "index.html?hold=references&row=Q4"),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.strip(self.port, f"q4.html?from={name}")[0], (address, f"Back to {label}"))

    def test_back_keeps_the_filters_of_a_hold_list_and_drops_them_elsewhere(self) -> None:
        link = self.strip(self.port, "q4.html?from=hold-conversations&project=Harbor&tag=lamps&search=Dock+lights")[0]
        self.assertEqual(link, ("index.html?hold=conversations&project=Harbor&tag=lamps&search=Dock+lights&row=Q4",
                                "Back to Hold conversations"))
        self.assertEqual(self.strip(self.port, "q4.html?from=needs-you&project=Harbor&search=Dock")[0],
                         ("index.html?tab=needs-you&row=Q4", "Back to Needs you"))

    def test_the_address_cannot_inject_markup_into_the_page(self) -> None:
        page = self.get(self.port, "q4.html?from=answered&project=%22%3E%3Cb%3Ex%3C%2Fb%3E&search=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
        self.assertNotIn("<b>x", page)
        self.assertNotIn("<script>alert", page)
        back = self.strips(page)[0][0][0]
        self.assertEqual(back, "index.html?hold=answered&project=%22%3E%3Cb%3Ex%3C%2Fb%3E&search=%3Cscript%3Ealert%281%29%3C%2Fscript%3E&row=Q4")

    def test_a_page_opened_directly_goes_back_to_the_desk_and_offers_no_next(self) -> None:
        for path in ("q2.html", "q2.html#Q2.1", "q2.html?from=", "q2.html?from=nowhere", "q2.html?tab=needs-you"):
            with self.subTest(path=path):
                self.assertEqual(self.strip(self.port, path), [("index.html", "Back to Desk")])

    def test_next_unread_in_needs_you_follows_the_list_order_and_opens_the_first_open_question(self) -> None:
        # Needs you lists the newest first: Q5, Q4, Q3, Q2, Q1.
        read = lambda path: self.strip(self.port, path)  # noqa: E731
        self.assertEqual(read("q3.html?from=needs-you")[1], ("q2.html?from=needs-you#Q2.1", "Next unread"))
        self.assertEqual(read("q2.html?from=needs-you")[1], ("q1.html?from=needs-you#Q1.1", "Next unread"))
        next_address, _ = read("q4.html?from=needs-you")[1]
        self.assertRegex(next_address, r"^q3[.]html[?]from=needs-you#Q3[.]1$")
        next_address, _ = read("q5.html?from=needs-you")[1]
        self.assertRegex(next_address, r"^q4[.]html[?]from=needs-you#reply-[0-9a-f]+$")

    def test_next_unread_wraps_from_the_end_of_a_list_to_its_start(self) -> None:
        address, words = self.strip(self.port, "q1.html?from=needs-you")[1]
        self.assertRegex(address, r"^q5[.]html[?]from=needs-you#reply-[0-9a-f]+$")
        self.assertEqual(words, "Next unread")

    def test_next_unread_in_other_lists_counts_only_unread_replies_and_keeps_the_list(self) -> None:
        # Conversations lists Q5 then Q4, and both are unread.
        address, _ = self.strip(self.port, "q5.html?from=conversations")[1]
        self.assertRegex(address, r"^q4[.]html[?]from=conversations#reply-[0-9a-f]+$")
        address, _ = self.strip(self.port, "q5.html?from=hold-conversations")[1]
        self.assertRegex(address, r"^q4[.]html[?]from=hold-conversations#reply-[0-9a-f]+$")

    def test_next_unread_is_absent_when_no_other_item_in_the_list_is_unread(self) -> None:
        self.read_replies("Q5")
        # Only Q4 is unread now. From Q5 it is next; from Q4 nothing else is unread.
        self.assertEqual([text for _, text in self.strip(self.port, "q5.html?from=conversations")], ["Back to Conversations", "Next unread"])
        self.assertEqual([text for _, text in self.strip(self.port, "q4.html?from=conversations")], ["Back to Conversations"])
        self.read_replies("Q4")
        for path in ("q5.html?from=conversations", "q4.html?from=hold-conversations", "q1.html?from=references",
                     "q3.html?from=answered", "q3.html?from=watch"):
            with self.subTest(path=path):
                self.assertNotIn("Next unread", self.get(self.port, path))

    def test_next_unread_skips_items_that_are_not_unread_and_reads_the_state_of_each_request(self) -> None:
        self.read_replies("Q4")
        self.assertEqual([text for _, text in self.strip(self.port, "q5.html?from=conversations")], ["Back to Conversations"])
        self.assertEqual(self.run_cli("post", "Q4", "--text", "More news.")[0], 0)
        self.assertEqual([text for _, text in self.strip(self.port, "q5.html?from=conversations")],
                         ["Back to Conversations", "Next unread"])

    def test_the_next_page_carries_the_list_so_the_owner_can_keep_going(self) -> None:
        first = self.strip(self.port, "q3.html?from=needs-you")[1][0]
        second = self.strip(self.port, first)[1][0]
        self.assertEqual((first, second), ("q2.html?from=needs-you#Q2.1", "q1.html?from=needs-you#Q1.1"))
        self.assertEqual(self.strip(self.port, second)[0][0], "index.html?tab=needs-you&row=Q1")

    def test_an_answered_item_keeps_its_place_in_needs_you_for_the_next_unread(self) -> None:
        # Q2 is answered and leaves Needs you, yet Next unread still follows the place where Q2 stood.
        self.answer("Q2.1")
        desk = self.get(self.port, "").split("<dialog")[0]
        self.assertNotIn('href="q2.html', desk.split('id="desk-panel-needs-you"')[1])
        self.assertEqual(self.strip(self.port, "q2.html?from=needs-you")[1][0], "q1.html?from=needs-you#Q1.1")
        self.assertEqual(self.strip(self.port, "q3.html?from=needs-you")[1][0], "q1.html?from=needs-you#Q1.1")

    def test_a_conversation_archived_from_its_page_keeps_its_place_in_conversations(self) -> None:
        self.run_cli("archive", "Q5")
        address, _ = self.strip(self.port, "q5.html?from=conversations")[1]
        self.assertRegex(address, r"^q4[.]html[?]from=conversations#reply-")
        self.assertEqual(self.strip(self.port, "q5.html?from=archived")[0][1], "Back to Archived")
        self.assertEqual([text for _, text in self.strip(self.port, "q5.html?from=archived")], ["Back to Archived"])

    def test_conversations_list_pinned_conversations_first(self) -> None:
        self.talk("Winter storage")  # Q6, the newest
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            next(item for item in data["items"] if item["id"] == "Q4")["pinned"] = True
        # Pinned first, then by latest activity: Q4, Q6, Q5.
        self.assertRegex(self.strip(self.port, "q4.html?from=conversations")[1][0], r"^q6[.]html")
        self.assertRegex(self.strip(self.port, "q6.html?from=conversations")[1][0], r"^q5[.]html")
        self.assertRegex(self.strip(self.port, "q5.html?from=conversations")[1][0], r"^q4[.]html")

    def test_the_strip_sits_at_both_ends_of_the_page(self) -> None:
        page = self.get(self.port, "q3.html?from=needs-you")
        self.assertEqual(len(self.strips(page)), 2)
        self.assertLess(page.index('<nav class="list-nav"'), page.index('<h1>'))
        self.assertGreater(page.index('<nav class="list-nav end"'), page.index('class="sendbar"'))
        self.assertEqual(len(self.strips(self.get(self.port, "q4.html?from=conversations"))), 2)


class ListRowLinkTests(ListContextCase):
    def setUp(self) -> None:
        super().setUp()
        self.questions("Pier lights", "--project", "Harbor")
        self.add("resource", "Calls redesign", "The new layout.", "--url", "https://example.com/calls", "--link-text", "Calls")
        self.add("list", "Harbor chores", "Chores.", "--body-file", "-", stdin="- Paint the hull\n")
        self.talk("Sail inventory")  # Q4
        self.talk("Dock lights")  # Q5
        self.port = self.start()

    def desk(self) -> str:
        return self.get(self.port, "")

    def test_every_desk_row_names_its_list_in_the_link_to_its_page(self) -> None:
        page = self.desk()
        needs_you = page.split('id="desk-panel-needs-you"')[1].split("</section>")[0]
        self.assertIn('class="card-link" href="q1.html?from=needs-you#Q1.1"', needs_you)
        self.assertRegex(needs_you, r'class="card-link" href="q5[.]html[?]from=needs-you#reply-')
        self.assertIn('<tr class="work next" data-id="Q1">', needs_you)
        conversations = page.split('id="desk-panel-conversations"')[1].split('id="desk-panel-needs-you"')[0]
        self.assertEqual(re.findall(r'class="topic-title" href="(q\d)[.]html[?]from=conversations#reply-', conversations), ["q5", "q4"])

    def test_an_archived_conversation_links_with_the_archived_list(self) -> None:
        self.run_cli("archive", "Q4")
        page = self.desk()
        archived = page.split('id="desk-panel-archived"')[1].split("</section>")[0]
        self.assertRegex(archived, r'class="topic-title" href="q4[.]html[?]from=archived#reply-')
        self.assertNotIn("from=conversations#reply-" + "q4", page)

    def test_every_hold_row_names_its_list_and_the_script_may_add_the_filters(self) -> None:
        page = self.desk()
        library = page.split("<dialog")[1]
        references = library.split('id="panel-references"')[1].split('id="panel-answered"')[0]
        self.assertIn('href="q3.html?from=references" data-detail>Harbor chores</a>', references)
        self.assertIn('class="lib-title ref-id" href="q2.html?from=references" data-detail>Q2</a>', references)
        self.assertNotIn("from=", re.search(r'class="ref-title" href="(https://[^"]+)"', references)[1])
        conversations = library.split('id="panel-conversations"')[1]
        self.assertEqual(re.findall(r'class="lib-title" href="(q\d)[.]html[?]from=hold-conversations#reply-[0-9a-f]+" data-detail', conversations),
                         ["q5", "q4"])

    def test_the_saved_pages_are_unchanged_because_they_have_no_lists_to_return_to(self) -> None:
        pages = quarterdeck.site_pages(self.store(), "Quarterdeck", "index.html")
        self.assertNotIn("from=", "".join(pages.values()))
        self.assertNotIn('<nav class="list-nav', "".join(pages.values()))

    def test_every_watch_row_names_the_watch_list_and_can_be_returned_to(self) -> None:
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            for item in data["items"]:
                if item["id"] == "Q4":
                    item["conversation"]["item"].append({"id": "a" * 32, "author": "you", "text": "Any news?", "at": "2026-11-26T10:00:00Z",
                                                         "delivery": "sent", "request_id": "quarterdeck-" + "b" * 32, "home": "c" * 64})
        watch = self.get(self.port, "watch.html")
        self.assertIn('<tr id="watch-Q4"><th scope="row"><a href="q4.html?from=watch">', watch)
        self.assertEqual(self.strip(self.port, "q4.html?from=watch")[0], ("watch.html#watch-Q4", "Back to Watch"))


class HoldFilterTests(ListContextCase):
    """Next unread inside a Hold list follows the project, tag and search of that list."""

    def setUp(self) -> None:
        super().setUp()
        self.talk("Harbor lamps")  # Q1
        self.talk("Quay signs")  # Q2
        self.talk("Harbor chart")  # Q3
        self.talk("Rope order")  # Q4
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            for item, project, tags in zip(data["items"], ("Harbor", "Quay", "Harbor", "Quay"), ([], ["rope"], ["rope"], ["rope"])):
                item["project"] = project
                if tags:
                    item["tags"] = tags
        self.port = self.start()

    def next_item(self, path: str) -> str | None:
        links = self.strip(self.port, path)
        return re.match(r"q\d+", links[1][0])[0].upper() if len(links) > 1 else None

    def test_the_project_narrows_the_list_the_next_unread_comes_from(self) -> None:
        # Hold conversations lists Q4, Q3, Q2, Q1; Harbor holds Q3 and Q1.
        self.assertEqual(self.next_item("q4.html?from=hold-conversations"), "Q3")
        self.assertEqual(self.next_item("q3.html?from=hold-conversations&project=Harbor"), "Q1")
        self.assertEqual(self.next_item("q1.html?from=hold-conversations&project=Harbor"), "Q3")

    def test_the_tag_and_both_filters_together_narrow_the_list(self) -> None:
        self.assertEqual(self.next_item("q4.html?from=hold-conversations&tag=rope"), "Q3")
        self.assertEqual(self.next_item("q3.html?from=hold-conversations&tag=rope"), "Q2")
        self.assertEqual(self.next_item("q3.html?from=hold-conversations&tag=rope&project=Quay"), "Q2")

    def test_the_search_narrows_the_list_by_words_and_by_item_ids(self) -> None:
        self.assertEqual(self.next_item("q3.html?from=hold-conversations&search=harbor"), "Q1")
        self.assertEqual(self.next_item("q3.html?from=hold-conversations&search=HARBOR+lamps"), "Q1")
        self.assertIsNone(self.next_item("q3.html?from=hold-conversations&search=chart"))
        self.assertEqual(self.next_item("q3.html?from=hold-conversations&search=Q1"), "Q1")
        self.assertEqual(self.next_item("q3.html?from=hold-conversations&search=q4"), "Q4")
        self.assertIsNone(self.next_item("q3.html?from=hold-conversations&search=nothing+like+it"))

    def test_the_item_itself_need_not_match_the_filters(self) -> None:
        self.assertEqual(self.next_item("q2.html?from=hold-conversations&project=Harbor"), "Q1")

    def test_a_conversation_read_in_the_meantime_is_skipped(self) -> None:
        self.read_replies("Q3")
        self.assertEqual(self.next_item("q4.html?from=hold-conversations"), "Q2")

    def test_hold_search_text_matches_what_the_list_script_matches(self) -> None:
        item = {"id": "Q8", "kind": "questions", "title": "Pier lights", "description": "Pick the lamp colour.",
                "body": {"questions": [{"question": "Which Straße?", "context": "c", "options": ["a"]}]}, "answers": {}}
        self.assertEqual(quarterdeck.library_search_text(item).split(), "pier lights pick the lamp colour. which strasse?".split())
        match = quarterdeck.library_matches
        self.assertTrue(match(item, {"search": "STRASSE lamp"}, topics=False))
        self.assertTrue(match(item, {"search": "q8.1"}, topics=False))
        self.assertFalse(match(item, {"search": "q8.2"}, topics=False))
        self.assertFalse(match(item, {"search": "q9"}, topics=False))
        self.assertTrue(match(item, {"search": "  "}, topics=False))


class ListContextBrowserTests(ListContextCase):
    def run_in_desk(self, steps: str, route: str, window_size: str = "390,844") -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the list context browser tests")
        page = base.WorkersTests.instrumented_page(quarterdeck.list_page, steps)
        with patch.object(quarterdeck, "list_page", page):
            outcome, stderr = base.WorkersTests.browser_page(self, self.start(), route, window_size=window_size)
        self.assertEqual(outcome, "PASS", stderr)

    def seed(self) -> None:
        for title in ("Pier lights", "Quay signs", "Rope order"):
            self.questions(title)
        for title in ("Sail inventory", "Dock lights"):
            self.talk(title)

    def test_back_opens_the_needs_you_tab_with_the_row_in_view_and_cleans_the_address(self) -> None:
        self.seed()
        self.run_in_desk("""
const tab = document.getElementById('desk-tab-needs-you');
check(tab.getAttribute('aria-selected') === 'true' && !document.getElementById('desk-panel-needs-you').hidden,
      'Back did not open Needs you');
check(document.getElementById('desk-panel-conversations').hidden, 'Conversations stayed open');
const link = document.querySelector('#desk-panel-needs-you tr[data-id="Q2"] .card-link');
check(document.activeElement === link, 'The row of Q2 did not take focus');
const box = link.getBoundingClientRect();
check(box.top >= 0 && box.bottom <= innerHeight, 'The row of Q2 is out of view');
check(location.search === '', 'The address kept its list parameters: ' + location.search);
check(document.documentElement.scrollWidth <= innerWidth, 'The page scrolls sideways');
""", "/qd/?tab=needs-you&row=Q2")

    def test_back_to_needs_you_lands_on_the_next_row_when_the_item_was_answered(self) -> None:
        self.seed()
        self.answer("Q2.1")
        self.run_in_desk("""
check(!document.querySelector('#desk-panel-needs-you tr[data-id="Q2"]'), 'Q2 is still on Needs you');
check(document.activeElement === document.querySelector('#desk-panel-needs-you tr[data-id="Q1"] .card-link'),
      'Focus did not land on the row below the answered question');
""", "/qd/?tab=needs-you&row=Q2")

    def test_back_opens_archived_and_conversations_at_the_row(self) -> None:
        self.seed()
        self.run_cli("archive", "Q4")
        self.run_in_desk("""
check(document.getElementById('desk-tab-archived').getAttribute('aria-selected') === 'true', 'Archived did not open');
check(document.activeElement === document.querySelector('#topics-archived [data-id="Q4"] .topic-title'),
      'The archived row did not take focus');
""", "/qd/?tab=archived&row=Q4")

    def test_an_unknown_tab_or_row_leaves_the_desk_as_it_opens(self) -> None:
        self.seed()
        self.run_in_desk("""
check(document.getElementById('desk-tab-conversations').getAttribute('aria-selected') === 'true', 'The first tab changed');
""", "/qd/?tab=nowhere&row=Q2")
        self.run_in_desk("""
check(document.getElementById('desk-tab-needs-you').getAttribute('aria-selected') === 'true', 'Needs you did not open');
check(document.activeElement === document.body, 'Something took focus for a row that does not exist');
""", "/qd/?tab=needs-you&row=nothing")

    def test_back_reopens_hold_on_its_tab_with_the_filters_and_the_row(self) -> None:
        self.talk("Harbor lamps")
        self.talk("Quay signs")
        self.talk("Harbor chart")
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            for item, project in zip(data["items"], ("Harbor", "Quay", "Harbor")):
                item["project"] = project
        self.run_in_desk("""
const dialog = document.getElementById('library');
const panel = document.getElementById('panel-conversations');
check(dialog.open, 'Hold did not open');
check(document.getElementById('tab-conversations').getAttribute('aria-selected') === 'true', 'The Conversations tab did not open');
check(panel.querySelector('select[name="project"]').value === 'Harbor', 'The project filter did not return');
check(panel.querySelector('input[type="search"]').value === 'harbor', 'The search did not return');
const shown = [...panel.querySelectorAll('.lib-item:not([hidden])')].map(row => row.dataset.id).join();
check(shown === 'Q3,Q1', 'The list did not apply the filters: ' + shown);
const row = panel.querySelector('[data-id="Q1"]');
check(document.activeElement === row.querySelector('a[data-detail]'), 'The row of Q1 did not take focus');
check(location.search === '', 'The address kept its list parameters');
""", "/qd/?hold=conversations&project=Harbor&search=harbor&row=Q1")

    def test_the_links_of_a_hold_list_follow_its_filters_as_they_change(self) -> None:
        self.talk("Harbor lamps")
        self.talk("Quay signs")
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            for item, project in zip(data["items"], ("Harbor", "Quay")):
                item["project"] = project
        self.run_in_desk("""
document.getElementById('open-library').click();
document.getElementById('tab-conversations').click();
const panel = document.getElementById('panel-conversations');
const link = panel.querySelector('[data-id="Q1"] a[data-detail]');
check(/^q1[.]html[?]from=hold-conversations#reply-/.test(link.getAttribute('href')), 'Unexpected start: ' + link.getAttribute('href'));
const project = panel.querySelector('select[name="project"]');
project.value = 'Harbor';
project.dispatchEvent(new Event('change', {bubbles: true}));
const search = panel.querySelector('input[type="search"]');
search.value = 'lamps  now';
search.dispatchEvent(new Event('input', {bubbles: true}));
const params = new URLSearchParams(link.getAttribute('href').split('#')[0].split('?')[1]);
check(params.get('from') === 'hold-conversations' && params.get('project') === 'Harbor' && params.get('search') === 'lamps now',
      'The link did not carry the filters: ' + link.getAttribute('href'));
check(link.getAttribute('href').includes('#reply-'), 'The link lost its place in the page');
search.value = '';
project.value = '';
search.dispatchEvent(new Event('input', {bubbles: true}));
check(/^q1[.]html[?]from=hold-conversations#reply-[0-9a-f]+$/.test(link.getAttribute('href')), 'Clearing the filters left them in the link');
""", "/qd/")
