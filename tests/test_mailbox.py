from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import uuid
from itertools import chain
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_quarterdeck as base  # noqa: E402

quarterdeck = base.quarterdeck

ONE_QUESTION = [{"question": "Which paper should the pocket guide use?", "context": "The printer needs the choice.",
                 "options": ["Matte", "Silk"]}]
TWO_QUESTIONS = ONE_QUESTION + [{"question": "Who signs off the final proof?", "context": "The printer needs one name.",
                                 "options": ["The harbor master", "The clerk"]}]


class MailboxCase(base.ImageCase):
    """The Desk as a mailbox: Conversations with Unread on top, For review for what waits for the owner, and Later."""

    start = base.ServeTests.start
    fetch = base.ServeTests.fetch
    post = base.ConversationTests.post
    inbox = base.ConversationTests.inbox
    begin = base.TalkToFirstmateTests.begin
    check_answer_page = base.ServeTests.check_answer_page

    def questions(self, title: str, body=None) -> str:
        return self.add("questions", title, "Choices to make.", "--body-file", "-", stdin=json.dumps(body or ONE_QUESTION))

    def desk(self, port) -> str:
        return self.fetch(port, "/qd/")[1].split("<dialog", 1)[0]

    def group(self, page: str, section: str) -> list[str]:
        """The IDs in one list of the Desk, in order."""
        found = re.search(rf'<section class="topics" id="{section}"[^>]*>(.*?)</section>', page, re.DOTALL)
        return re.findall(r'data-(?:id|notice)="([QC]\d+)"', found[1])

    def counts(self, page: str) -> dict[str, str]:
        return dict(re.findall(r'>(Conversations|For review) <span class="tab-count"(?: hidden)?>(\d*)</span>', page))

    def item(self, item_id: str) -> dict:
        return next(item for item in self.store()["items"] if item["id"] == item_id)

    def firstmate(self, command: str, *argv: str) -> None:
        code, _, err = self.run_cli(command, *argv)
        self.assertEqual(code, 0, err)

    def read(self, item_id: str) -> None:
        ids = [m["id"] for m in self.item(item_id)["conversation"]["item"] if m["author"] == "firstmate"]
        self.assertEqual(self.post(self.port, "/qd/replies/read", {"part": item_id, "ids": ids})[0], 200)


class MembershipTests(MailboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.port = self.start("/qd", ask_home=self.inbox())

    def test_for_review_holds_only_what_waits_and_an_unread_reply_stays_in_conversations(self) -> None:
        self.questions("Pocket guide paper")  # Q1
        first, _ = self.begin(self.port, "Bell schedule", "When does the bell ring?")  # Q2
        self.firstmate("reply", "Q2", "--ask", first["id"], "--text", "At nine.")
        page = self.desk(self.port)
        self.assertEqual(self.group(page, "review-waiting"), ["Q1"])
        self.assertEqual(self.group(page, "topics-unread"), ["Q2"])
        self.assertEqual(self.group(page, "topics-recent"), [])
        self.assertEqual(self.counts(page), {"Conversations": "1", "For review": "1"})
        self.assertIsNone(quarterdeck.attention_summary(self.item("Q2"), served=True))
        self.read("Q2")
        page = self.desk(self.port)
        self.assertEqual((self.group(page, "topics-unread"), self.group(page, "topics-recent")), ([], ["Q2"]))
        self.assertEqual(self.counts(page), {"Conversations": "", "For review": "1"})

    def test_firstmate_marks_a_message_for_review_and_the_owners_message_ends_the_wait(self) -> None:
        first, _ = self.begin(self.port, "Lighthouse photo rights", "Can we use the archive's photos?")
        self.firstmate("reply", "Q1", "--ask", first["id"], "--text", "Print is covered. Do you want web use as well?", "--for-review")
        reply = self.item("Q1")["conversation"]["item"][-1]
        self.assertIs(reply["for_review"], True)
        quarterdeck.check_store(self.store())
        page = self.desk(self.port)
        # It waits in For review with Asked. In Conversations it keeps its place by pin and activity, out of Unread.
        self.assertEqual((self.group(page, "review-waiting"), self.group(page, "topics-unread"), self.group(page, "topics-recent")),
                         (["Q1"], [], ["Q1"]))
        self.assertEqual(self.counts(page), {"Conversations": "", "For review": "1"})
        waiting = page.split('id="review-waiting"')[1].split("</section>")[0]
        self.assertIn(f'<a class="topic-title" href="q1.html?from=needs-you#reply-{reply["id"]}"><span class="id">Q1</span> Lighthouse photo rights</a>', waiting)
        self.assertIn('<span class="badge">Unread reply</span><span>Asked</span>', waiting)
        self.assertIn('<p class="topic-latest">Print is covered. Do you want web use as well?</p>', waiting)
        row = re.search(r'<li class="topic" data-id="Q1".*?</li>', page, re.DOTALL)[0]
        self.assertIn('<span class="badge">Unread reply</span><span>Asked</span>', row)
        # Reading it does not end the wait; a message of the owner's does.
        self.read("Q1")
        self.assertEqual(self.group(self.desk(self.port), "review-waiting"), ["Q1"])
        body = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Yes, web as well.", "home": first["home"]}
        self.assertEqual(self.post(self.port, "/qd/asks", body)[0], 200)
        page = self.desk(self.port)
        self.assertEqual(self.group(page, "review-waiting"), [])
        self.assertIn("Nothing is waiting for your review.", page)
        self.assertIn('<p class="topic-latest">You: Yes, web as well.</p>', page)

    def test_post_and_converse_take_the_mark_and_resolving_ends_the_wait(self) -> None:
        self.firstmate("converse", "--title", "Harbor map legend", "--text", "The legend is ready for your review.", "--for-review")
        self.firstmate("converse", "--title", "Bell schedule", "--text", "The bells are live.")
        self.firstmate("post", "Q2", "--text", "Tell me if Sunday should differ.", "--for-review")
        self.assertEqual(self.group(self.desk(self.port), "review-waiting"), ["Q2", "Q1"])
        self.assertEqual(self.run_cli("resolve", "Q1")[0], 0)
        self.assertEqual(self.group(self.desk(self.port), "review-waiting"), ["Q2"])
        self.assertFalse(quarterdeck.asks_owner(self.item("Q1")))
        self.firstmate("post", "Q2", "--text", "No change needed after all.")  # an update without the mark keeps the wait
        self.assertTrue(quarterdeck.asks_owner(self.item("Q2")))

    def test_replaying_a_start_preserves_archived_navigation_and_hold_membership(self) -> None:
        initial, _ = self.begin(self.port, "Bell schedule", "When does the bell ring?")
        self.firstmate("archive", "Q1")
        status, result = self.post(self.port, "/qd/conversations", initial)
        self.assertEqual(status, 200)
        self.assertIn('href="q1.html?from=archived"', result["row"])
        self.assertEqual(result["hold"], "")
        self.assertEqual(self.group(self.desk(self.port), "topics-archived"), ["Q1"])

    def test_a_review_row_keeps_pointing_to_the_outstanding_request(self) -> None:
        self.firstmate("converse", "--title", "Bell schedule", "--text", "Should Sunday differ?", "--for-review")
        request = self.item("Q1")["conversation"]["item"][0]["id"]
        self.read("Q1")
        self.firstmate("post", "Q1", "--text", "Saturday is settled.")
        waiting = self.desk(self.port).split('id="review-waiting"', 1)[1].split("</section>", 1)[0]
        self.assertIn(f'q1.html?from=needs-you#reply-{request}', waiting)
        self.assertIn('<p class="topic-latest">Should Sunday differ?</p>', waiting)
        self.assertIn(f'class="msg firstmate" data-reply-id="{request}"', self.fetch(self.port, "/qd/q1.html")[1])

    def test_the_mark_is_for_conversations_and_the_help_names_it(self) -> None:
        self.questions("Pocket guide paper")
        first = self.post(self.port, "/qd/asks", {"part": "Q1.1", "id": uuid.uuid4().hex, "text": "Is silk dearer?",
                                                 "home": re.search(r'data-home="([^"]+)"', self.fetch(self.port, "/qd/q1.html")[1])[1]})
        self.assertEqual(first[0], 200)
        ask = self.item("Q1")["conversation"]["1"][0]["id"]
        before = self.store()
        code, _, err = self.run_cli("reply", "Q1.1", "--ask", ask, "--text", "A little.", "--for-review")
        self.assertEqual(code, 2)
        self.assertIn("--for-review needs a conversation", err)
        self.assertEqual(self.store(), before)
        for command in ("reply", "post", "converse"):
            self.assertIn("--for-review", self.run_cli("help", command)[1])

    def test_the_store_refuses_a_damaged_mark_or_later_flag(self) -> None:
        first, _ = self.begin(self.port, "Bell schedule", "When does the bell ring?")
        self.firstmate("reply", "Q1", "--ask", first["id"], "--text", "At nine.", "--for-review")
        self.questions("Pocket guide paper")
        self.add("resource", "Tide chart", "A chart.", "--url", "https://example.com/tides", "--link-text", "Chart")
        good = self.store()
        good["items"][0]["later"] = True
        good["items"][1]["later"] = True
        quarterdeck.check_store(json.loads(json.dumps(good)))

        def damaged(change) -> None:
            data = json.loads(json.dumps(good))
            change(data["items"])
            with self.assertRaises(ValueError):
                quarterdeck.check_store(data)

        damaged(lambda items: items[0]["conversation"]["item"][1].update(for_review=False))
        damaged(lambda items: items[0]["conversation"]["item"][0].update(for_review=True))  # the owner's own message
        damaged(lambda items: items[0].update(later=False))
        damaged(lambda items: items[1].update(later="yes"))
        damaged(lambda items: items[2].update(later=True))  # a resource cannot wait for the owner

    def test_notices_show_under_unread_and_count_on_conversations(self) -> None:
        self.questions("Pocket guide paper")
        self.firstmate("catch-up", "add", "--title", "Ferry timetable is published", "--summary", "The winter timetable is out.",
                       "--item", "Q1", "--url", "https://example.com/timetable", "--link-text", "Open the timetable")
        self.firstmate("catch-up", "add", "--title", "Second notice", "--summary", "More news.")
        page = self.desk(self.port)
        self.assertEqual(self.group(page, "topics-unread"), ["C2", "C1"])
        self.assertEqual(self.counts(page), {"Conversations": "2", "For review": "1"})
        self.assertNotIn("notice", page.split('id="desk-panel-needs-you"')[1].split('id="desk-panel-under-way"')[0])
        self.assertIn('<a href="q1.html?from=conversations" data-pane>Pocket guide paper</a>'
                      '<a href="https://example.com/timetable" target="_blank" rel="noopener noreferrer">Open the timetable</a>', page)
        self.assertIn('<button type="button" data-read="C1" aria-label="Mark read, Ferry timetable is published">Mark read</button>', page)
        self.assertRegex(page, r'<button type="button" data-read-all>Mark all notices read</button>')
        self.assertIn('id="no-conversations" hidden', page)  # a notice is something to read, so the list is not empty

    def test_conversation_rows_carry_a_title_preview_and_status(self) -> None:
        first, _ = self.begin(self.port, "Bell schedule", "When does the bell ring?")
        self.begin(self.port, "Quay signs", "Navy or black?")
        self.firstmate("reply", "Q1", "--ask", first["id"], "--text", "At nine.")
        self.read("Q1")
        page = self.desk(self.port)
        rows = {item: re.search(rf'<li class="topic" data-id="{item}".*?</li>', page, re.DOTALL)[0] for item in ("Q1", "Q2")}
        for row in rows.values():
            self.assertNotIn("<button", row)
        self.assertIn('<span>Replied</span>', rows["Q1"])
        self.assertIn('<p class="topic-latest">You: Navy or black?</p>', rows["Q2"])
        self.assertNotIn('2 messages', rows["Q1"])
        self.assertNotIn("<h1>Desk</h1>", page)


class ReferencesTests(MailboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.port = self.start("/qd", ask_home=self.inbox())

    def test_links_leaving_the_desk_open_in_a_new_tab_and_links_inside_it_do_not(self) -> None:
        self.add("resource", "Port tide tables", "See https://example.com/notes for more.",
                 "--url", "https://example.com/tides", "--link-text", "Tide tables",
                 "--url", "https://example.com/datum", "--link-text", "Datum")  # Q1
        self.add("list", "Proof run checklist", "What to check.", "--body-file", "-", stdin="- Confirm the paper\n")  # Q2
        for page in (self.desk(self.port), self.fetch(self.port, "/qd/q1.html")[1]):
            anchors = re.findall(r"<a\b[^>]*>", page)
            external = [a for a in anchors if re.search(r'href="https?://', a)]
            internal = [a for a in anchors if re.search(r'href="(?!https?://)', a)]
            self.assertTrue(external and internal)
            for anchor in external:
                self.assertIn('target="_blank"', anchor)
                self.assertIn('rel="noopener noreferrer"', anchor)
            for anchor in internal:
                self.assertNotIn("_blank", anchor)

    def test_references_have_their_own_tab_beside_conversations_and_for_review(self) -> None:
        page = self.desk(self.port)
        self.assertEqual(re.findall(r'role="tab" id="desk-tab-([a-z-]+)"', page),
                         ["conversations", "needs-you", "references", "under-way", "archived"])
        self.assertIn('>References</button>', page)
        self.assertIn('<p class="empty" id="no-references">No references are saved yet. '
                      'A link or a list that Firstmate saves for you shows here.</p>', page)
        self.add("resource", "Port tide tables", "The official tables.", "--project", "Harbor",
                 "--url", "https://example.com/tides", "--link-text", "Tide tables",
                 "--url", "https://example.com/datum", "--link-text", "Datum <notes>")  # Q1
        self.add("list", "Proof run checklist", "What to check before the proof run.", "--body-file", "-",
                 stdin="- Confirm the paper\n- Sign off the legend\n")  # Q2
        self.questions("Pocket guide paper")  # Q3 is not a reference
        page = self.desk(self.port)
        self.assertEqual(self.group(page, "topics-references"), ["Q2", "Q1"])  # newest first
        self.assertIn('id="no-references" hidden', page)
        panel = page.split('id="desk-panel-references"')[1].split('id="desk-panel-under-way"')[0]
        # A resource's title goes straight to what it saves; its ID opens the saved item. A list's title opens the list.
        self.assertIn('<a class="bookmark-title" href="https://example.com/tides" target="_blank" rel="noopener noreferrer">Port tide tables</a>', panel)
        self.assertIn('<p class="notice-links"><a href="https://example.com/datum" target="_blank" rel="noopener noreferrer">Datum &lt;notes&gt;</a></p>', panel)
        self.assertIn('<a class="bookmark-id" href="q1.html?from=desk-references" data-pane aria-label="Q1, the saved item">Q1</a>'
                      '<span class="project-name">Harbor</span>', panel)
        self.assertIn('<a class="bookmark-title" href="q2.html?from=desk-references" data-pane>Proof run checklist</a>', panel)
        self.assertIn('<p class="topic-latest">What to check before the proof run.</p>', panel)
        self.assertNotIn("<button", panel)
        self.assertNotIn("Pocket guide paper", panel)
        self.assertEqual(self.counts(page), {"Conversations": "", "For review": "1"})  # a reference asks nothing

    def test_a_reference_page_goes_back_to_the_references_tab(self) -> None:
        self.add("list", "Proof run checklist", "What to check.", "--body-file", "-", stdin="- Confirm the paper\n")
        self.assertEqual(quarterdeck.parse_list_context("from=desk-references"), {"from": "desk-references"})
        page = self.fetch(self.port, "/qd/q1.html?from=desk-references")[1]
        self.assertIn('<a class="back-link" href="index.html?tab=references&amp;row=Q1">Back to References</a>', page)
        self.assertNotIn("Next unread", page)
        self.assertNotIn('class="later"', page)
        self.assertEqual([item["id"] for item in quarterdeck.list_members(self.store(), {"from": "desk-references"})], ["Q1"])

    def test_hold_still_holds_references_and_answered(self) -> None:
        self.add("resource", "Port tide tables", "The official tables.", "--url", "https://example.com/tides", "--link-text", "Tide tables")
        self.questions("Pocket guide paper")
        self.assertEqual(self.run_cli("mark", "Q2", "answered", "--via", "chat")[0], 0)
        hold = self.fetch(self.port, "/qd/")[1].split('<dialog id="library"', 1)[1]
        self.assertIn('href="q1.html?from=references" data-detail>Q1</a>', hold)
        self.assertIn('href="q2.html?from=answered" data-detail>Pocket guide paper</a>', hold)


class LaterTests(MailboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.port = self.start("/qd", ask_home=self.inbox())
        self.questions("Mooring fee table", TWO_QUESTIONS)  # Q1
        self.questions("Pocket guide paper")  # Q2
        self.questions("Harbor map first edition", TWO_QUESTIONS)  # Q3

    def later(self, item_id: str, value: bool = True):
        return self.post(self.port, "/qd/laters", {"part": item_id, "later": value})

    def links(self, path: str) -> list[tuple[str, str]]:
        page = self.fetch(self.port, f"/qd/{path}")[1]
        nav = re.search(r'<nav class="list-nav".*?</nav>', page, re.DOTALL)[0]
        return [(href.replace("&amp;", "&"), text) for href, text in re.findall(r'href="([^"]+)">([^<]+)</a>', nav)]

    def test_deal_with_later_puts_an_item_under_later_and_out_of_the_count(self) -> None:
        before = self.store()
        self.assertEqual(self.later("Q3"), (200, {"later": True}))
        after = self.store()
        self.assertIs(after["items"][2].pop("later"), True)
        self.assertEqual(after, before)
        page = self.desk(self.port)
        self.assertEqual((self.group(page, "review-waiting"), self.group(page, "review-later")), (["Q2", "Q1"], ["Q3"]))
        self.assertEqual(self.counts(page)["For review"], "2")
        self.assertIn('<div class="topics-head"><h2>Later</h2></div>', page)
        self.assertIn('<li class="topic review" data-id="Q3" data-unread="false">', page)
        # Next unread walks what waits and passes over Later.
        self.assertEqual(self.links("q2.html?from=needs-you")[1], ("q1.html?from=needs-you#Q1.1", "Next unread"))
        self.assertEqual(self.links("q1.html?from=needs-you")[1], ("q2.html?from=needs-you#Q2.1", "Next unread"))
        self.assertEqual(self.links("q3.html?from=needs-you")[1][1], "Next unread")
        self.assertEqual(self.later("Q3"), (200, {"later": True}))  # saying it twice changes nothing
        self.assertEqual(self.later("Q3", False), (200, {"later": False}))
        self.assertEqual(self.store(), before)
        self.assertEqual(self.group(self.desk(self.port), "review-waiting"), ["Q3", "Q2", "Q1"])
        self.assertIn('id="review-later" hidden', self.desk(self.port))

    def test_the_item_page_offers_the_button_only_while_the_item_waits(self) -> None:
        page = self.fetch(self.port, "/qd/q2.html?from=needs-you")[1]
        button = '<button type="button" class="later" data-id="Q2" data-title="Pocket guide paper" data-later="false">Deal with later</button>'
        # It sits with Back and Next unread at both ends of the page, and stays out of the send bar.
        self.assertEqual(page.count(button), 2)
        self.assertIn(f'Next unread</a>{button}</nav>', page)
        self.assertIn(f'{button}<p class="meta later-status" role="status"></p></nav>', page)
        self.assertNotIn("later", re.search(r'<div class="sendbar">.*?</output></div>', page, re.DOTALL)[0])
        self.later("Q2")
        self.assertIn('data-later="true">Remove from Later</button>', self.fetch(self.port, "/qd/q2.html")[1])
        self.later("Q2", False)
        self.assertEqual(self.run_cli("mark", "Q2", "answered", "--via", "chat")[0], 0)
        self.assertNotIn('class="later"', self.fetch(self.port, "/qd/q2.html")[1])  # nothing left to put aside
        first, _ = self.begin(self.port, "Bell schedule", "When does the bell ring?")
        self.assertNotIn('class="later"', self.fetch(self.port, "/qd/q4.html")[1])
        self.firstmate("reply", "Q4", "--ask", first["id"], "--text", "At nine, unless you say otherwise.", "--for-review")
        self.assertIn('data-later="false">Deal with later</button>', self.fetch(self.port, "/qd/q4.html")[1])

    def test_a_reply_lifts_a_later_item_back_up_until_it_is_read(self) -> None:
        home = re.search(r'data-home="([^"]+)"', self.fetch(self.port, "/qd/q3.html")[1])[1]
        ask = {"part": "Q3.1", "id": uuid.uuid4().hex, "text": "Is silk dearer?", "home": home}
        self.assertEqual(self.post(self.port, "/qd/asks", ask)[0], 200)
        self.later("Q3")
        self.assertEqual(self.group(self.desk(self.port), "review-later"), ["Q3"])
        self.firstmate("reply", "Q3.1", "--ask", ask["id"], "--text", "A little.")
        page = self.desk(self.port)
        self.assertEqual((self.group(page, "review-waiting"), self.group(page, "review-later")), (["Q3", "Q2", "Q1"], []))
        self.assertEqual(self.counts(page)["For review"], "3")
        reply = self.item("Q3")["conversation"]["1"][1]["id"]
        self.assertEqual(self.post(self.port, "/qd/replies/read", {"part": "Q3.1", "ids": [reply]})[0], 200)
        self.assertEqual(self.group(self.desk(self.port), "review-later"), ["Q3"])  # read, it waits in Later again

    def test_a_message_of_the_owners_takes_a_conversation_out_of_later(self) -> None:
        first, _ = self.begin(self.port, "Bell schedule", "When does the bell ring?")
        self.firstmate("reply", "Q4", "--ask", first["id"], "--text", "At nine, unless you say otherwise.", "--for-review")
        self.later("Q4")
        self.assertEqual(self.group(self.desk(self.port), "review-later"), ["Q4"])
        body = {"part": "Q4", "id": uuid.uuid4().hex, "text": "Nine is fine.", "home": first["home"]}
        self.assertEqual(self.post(self.port, "/qd/asks", body)[0], 200)
        self.assertNotIn("later", self.item("Q4"))
        self.assertNotIn("Q4", self.desk(self.port).split('id="desk-panel-needs-you"')[1].split('id="desk-panel-under-way"')[0])

    def test_deferring_ignores_existing_unread_replies_and_resolution_ends_the_cycle(self) -> None:
        self.firstmate("converse", "--title", "Bell schedule", "--text", "Should Sunday differ?", "--for-review")
        self.firstmate("post", "Q4", "--text", "Saturday is settled.")
        self.assertEqual(self.later("Q4"), (200, {"later": True}))
        self.assertEqual(self.group(self.desk(self.port), "review-later"), ["Q4"])
        self.firstmate("post", "Q4", "--text", "The holiday schedule arrived.")
        latest = self.item("Q4")["conversation"]["item"][-1]["id"]
        self.assertEqual(self.group(self.desk(self.port), "review-later"), [])
        self.assertEqual(self.post(self.port, "/qd/replies/read", {"part": "Q4", "ids": [latest]})[0], 200)
        self.assertEqual(self.group(self.desk(self.port), "review-later"), ["Q4"])
        self.firstmate("resolve", "Q4")
        self.firstmate("reopen", "Q4")
        self.assertEqual(self.group(self.desk(self.port), "review-later"), [])
        self.assertIn("Q4", self.group(self.desk(self.port), "review-waiting"))

    def test_closing_a_deferred_question_set_ends_its_review_cycle(self) -> None:
        self.assertEqual(self.later("Q3")[0], 200)
        self.firstmate("mark", "Q3", "closed")
        status, page, headers = self.fetch(self.port, "/qd/q3.html")
        self.assertEqual((status, headers.get("Content-Security-Policy")), (200, "frame-ancestors 'self'"))
        self.assertNotIn('class="later"', page)
        self.firstmate("mark", "Q3.1", "open")
        self.assertIn("Q3", self.group(self.desk(self.port), "review-waiting"))

    def test_later_refuses_other_items_bad_fields_and_other_origins(self) -> None:
        self.add("resource", "Tide chart", "A chart.", "--url", "https://example.com/tides", "--link-text", "Chart")
        before = self.store()
        for payload in ({"part": "Q4", "later": True}, {"part": "Q9", "later": True}, {"part": "q1", "later": True},
                        {"part": "Q1.1", "later": True}, {"part": "Q1", "later": "yes"}, {"part": "Q1"},
                        {"part": "Q1", "later": True, "extra": 1}, ["Q1"]):
            with self.subTest(payload=payload):
                status, message = self.post(self.port, "/qd/laters", payload)
                self.assertEqual((status, message), (400, "Check the conversation and later."))
        self.assertEqual(self.post(self.port, "/qd/laters", {"part": "Q1", "later": True}, origin="https://example.org")[0], 403)
        self.assertEqual(self.store(), before)


class ThreadTests(MailboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.port = self.start("/qd", ask_home=self.inbox())

    def long_thread(self) -> dict:
        """Q1 with six messages: you, a reply, an update, you, a reply, and an update that is still unread."""
        first, _ = self.begin(self.port, "Tide chart review", "Please check the tide charts.")
        self.firstmate("reply", "Q1", "--ask", first["id"], "--text", "I have the port tables.")
        self.firstmate("post", "Q1", "--text", "North pier matches.")
        second = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Check South basin first.", "home": first["home"]}
        self.assertEqual(self.post(self.port, "/qd/asks", second)[0], 200)
        self.firstmate("reply", "Q1", "--ask", second["id"], "--text", "South basin used the old survey.")
        self.read("Q1")
        self.firstmate("post", "Q1", "--text", "The review is finished.")
        return first

    def messages(self, page: str) -> list[str]:
        thread = page.split('<div class="thread-messages">')[1].split("</ol>")[0]
        states = []
        for name in re.findall(r'<li class="(msg [^"]*)"', thread):
            states.append("folded" if "folded" in name else "open")
        return states

    def test_earlier_read_messages_each_fold_to_one_line(self) -> None:
        self.long_thread()
        page = self.fetch(self.port, "/qd/q1.html")[1]
        self.assertEqual(self.messages(page), ["folded"] * 5 + ["open"])
        self.assertRegex(page, r'<li class="msg you folded" data-ask-id="[0-9a-f]{32}" id="message-1" tabindex="-1">'
                               r'<button type="button" class="fold" aria-expanded="false"><strong>You</strong> '
                               r'<span class="snip">Please check the tide charts[.]</span> <time datetime="[^"]+">[^<]+</time></button>'
                               r'<div class="msg-body" hidden><p class="who">')
        last = page.split('<div class="thread-messages">')[1].split("</ol>")[0].rsplit("<li", 1)[1]
        self.assertIn("data-unread", last)
        self.assertIn("The review is finished.", last)
        self.assertNotIn('class="fold"', last)

    def test_unread_replies_and_undelivered_messages_stay_open(self) -> None:
        first = self.long_thread()
        self.firstmate("post", "Q1", "--text", "One more thing.")
        self.inbox(accept=False)
        pending = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Thanks.", "home": first["home"]}
        self.assertEqual(self.post(self.port, "/qd/asks", pending)[1]["message"], "Message saved. Not delivered.")
        self.firstmate("post", "Q1", "--text", "Noted.")
        page = self.fetch(self.port, "/qd/q1.html")[1]
        # Five read messages fold, two unread updates stay open, the undelivered message keeps its retry in view.
        self.assertEqual(self.messages(page), ["folded"] * 5 + ["open"] * 4)
        self.assertIn(f'data-retry-ask="{pending["id"]}"', page)

    def test_a_single_message_stays_open(self) -> None:
        first, _ = self.begin(self.port, "Bell schedule", "When does the bell ring?")
        self.assertEqual(self.messages(self.fetch(self.port, "/qd/q1.html")[1]), ["open"])
        self.firstmate("reply", "Q1", "--ask", first["id"], "--text", "At nine.")
        page = self.fetch(self.port, "/qd/q1.html")[1]
        self.assertEqual(self.messages(page), ["folded", "open"])
        self.assertNotIn('class="show-earlier"', page)

    def test_sending_a_message_returns_the_thread_folded_the_same_way(self) -> None:
        first = self.long_thread()
        body = {"part": "Q1", "id": uuid.uuid4().hex, "text": "Thank you.", "home": first["home"]}
        status, result = self.post(self.port, "/qd/asks", body)
        self.assertEqual(status, 200)
        self.assertEqual(result["thread"].count('class="fold"'), 5)
        self.assertTrue(result["thread"].rstrip().endswith("</li></ol>"))
        self.assertNotIn("folded", result["thread"].rsplit("<li", 1)[1])

    def test_folded_lines_open_in_a_browser_and_an_address_reveals_its_message(self) -> None:
        self.long_thread()
        self.check_answer_page(self.port, r"""
const list = document.querySelector('.thread-messages .exchange');
const state = () => [...list.children].map(li => li.classList.contains('folded') ? 'folded' : 'open').join();
check(state() === 'folded,folded,folded,folded,folded,open', 'The thread did not start folded: ' + state());
const first = list.querySelector('#message-1');
const fold = first.querySelector(':scope > .fold');
const body = first.querySelector(':scope > .msg-body');
fold.click();
check(state().startsWith('open,folded') && !body.hidden && fold.getAttribute('aria-expanded') === 'true'
      && first.querySelector('.record, p:not(.who)'), 'A folded line did not open in place');
fold.click();
check(state() === 'folded,folded,folded,folded,folded,open' && body.hidden && fold.getAttribute('aria-expanded') === 'false',
      'The fold control did not fold an opened message back: ' + state());
fold.click();
const text = body.querySelector('.record, p:not(.who)');
const link = document.createElement('a');
link.href = '#message-1';
body.querySelector(':scope > .who').append(link);
link.addEventListener('click', event => event.preventDefault());
link.click();
check(state().startsWith('open'), 'Clicking a link folded the message');
getSelection().selectAllChildren(text);
text.click();
check(state().startsWith('open'), 'Selecting text folded the message');
getSelection().removeAllRanges();
text.click();
check(state().startsWith('open'), 'Clicking the message text folded the message');
text.click();
text.click();
text.dispatchEvent(new MouseEvent('dblclick', {bubbles: true, detail: 2}));
check(state().startsWith('open'), 'Double-clicking the message text folded the message');
body.querySelector(':scope > .who').click();
check(state().startsWith('folded') && fold.getAttribute('aria-expanded') === 'false', 'Clicking the header line did not fold the message');
fold.focus();
fold.click();
check(state().startsWith('open') && document.activeElement === fold, 'The fold control lost focus');
fold.click();
check(state().startsWith('folded') && document.activeElement === fold, 'The fold control did not fold again');
check(fold.tagName === 'BUTTON', 'The fold control is not a button, so Enter and Space would not work');
fold.click();
check(document.documentElement.scrollWidth <= innerWidth, 'The thread scrolled sideways');
fold.click();
location.hash = '#message-2';
await new Promise(resolve => setTimeout(resolve, 50));
check(!document.getElementById('message-2').classList.contains('folded'), 'An address did not open the message it names');
const second = document.getElementById('message-2');
second.querySelector(':scope > .fold').click();
check(second.classList.contains('folded'), 'The named message did not fold back');
const again = [...document.querySelectorAll('a[href="#message-2"]')].find(a => !second.contains(a));
check(again && location.hash === '#message-2', 'The thread has no other link to message 2');
again.click();
check(!second.classList.contains('folded'), 'A link to the address already shown did not reopen its message');
""", viewport=(390, 844))

    def test_an_opened_message_folds_back_in_the_wide_reading_pane(self) -> None:
        self.long_thread()
        self.check_answer_page(self.port, r"""
const first = document.getElementById('message-1');
const fold = first.querySelector(':scope > .fold');
fold.click();
check(first.querySelector(':scope > .msg-body').hidden === false, 'A folded line did not open');
fold.click();
check(first.classList.contains('folded') && fold.getAttribute('aria-expanded') === 'false', 'An opened message did not fold back');
""", viewport=(1440, 900))


class ScopeTests(MailboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.port = self.start("/qd", ask_home=self.inbox())
        for title in ("Sail inventory", "Quay signs", "Dock lights"):  # Q1, Q2, Q3: each opens unread
            self.firstmate("converse", "--title", title, "--text", f"Update on {title.lower()}.")
        self.read("Q2")

    def links(self, path: str) -> list[tuple[str, str]]:
        page = self.fetch(self.port, f"/qd/{path}")[1]
        nav = re.search(r'<nav class="list-nav".*?</nav>', page, re.DOTALL)[0]
        return [(href.replace("&amp;", "&"), text) for href, text in re.findall(r'href="([^"]+)">([^<]+)</a>', nav)]

    def test_unread_only_is_part_of_the_list_a_page_goes_back_to_and_steps_through(self) -> None:
        self.assertEqual(quarterdeck.parse_list_context("from=conversations&unread=1"), {"from": "conversations", "unread": "1"})
        context = {"from": "conversations", "unread": "1"}
        self.assertEqual([item["id"] for item in quarterdeck.list_members(self.store(), context)], ["Q3", "Q1"])
        back, following = self.links("q3.html?from=conversations&unread=1")
        self.assertEqual(back, ("index.html?tab=conversations&unread=1&row=Q3", "Back to Conversations"))
        self.assertRegex(following[0], r"^q1[.]html[?]from=conversations&unread=1&at=reply-[0-9a-f]+#reply-[0-9a-f]+$")
        # The read conversation is out of the filtered list. It stands after the unread ones, so Next unread starts over.
        self.assertRegex(self.links("q2.html?from=conversations&unread=1")[1][0], r"^q3[.]html[?]from=conversations&unread=1&at=reply-[0-9a-f]+#reply-")

    def test_the_list_puts_unread_first_then_pinned_then_the_rest(self) -> None:
        self.assertEqual(self.post(self.port, "/qd/pins", {"part": "Q2", "pinned": True})[0], 200)
        self.firstmate("converse", "--title", "Rope order", "--text", "Ordered.")  # Q4
        self.read("Q4")
        members = quarterdeck.list_members(self.store(), {"from": "conversations"})
        self.assertEqual([item["id"] for item in members], ["Q3", "Q1", "Q2", "Q4"])
        page = self.desk(self.port)
        self.assertEqual((self.group(page, "topics-unread"), self.group(page, "topics-pinned"), self.group(page, "topics-recent")),
                         (["Q3", "Q1"], ["Q2"], ["Q4"]))
        self.assertLess(page.index('id="topics-unread"'), page.index('id="topics-pinned"'))

    def test_an_archived_conversation_with_an_unread_reply_still_shows_under_unread(self) -> None:
        self.assertEqual(self.run_cli("archive", "Q3")[0], 0)
        page = self.desk(self.port)
        self.assertEqual(self.group(page, "topics-unread"), ["Q3", "Q1"])
        self.assertEqual(self.group(page, "topics-archived"), ["Q3"])
        self.assertIn('href="q3.html?from=archived#reply-', page)
        self.assertIn('href="q3.html?from=conversations&amp;at=reply-', page)
        self.assertRegex(self.links("q1.html?from=conversations&unread=1")[1][0], r"^q3[.]html[?]from=conversations&unread=1&at=reply-[0-9a-f]+#reply-")
        self.assertRegex(self.links("q3.html?from=conversations&unread=1")[1][0], r"^q1[.]html[?]from=conversations&unread=1&at=reply-[0-9a-f]+#reply-")

    def test_next_unread_does_not_return_to_a_reply_whose_read_is_pending(self) -> None:
        self.firstmate("post", "Q3", "--text", "The west dock is ready too.")
        second = self.item("Q3")["conversation"]["item"][-1]["id"]
        _, following = self.links(f"q3.html?from=conversations&at=reply-{second}")
        self.assertRegex(following[0], r"^q1[.]html[?]from=conversations&at=reply-")

    def test_a_list_refresh_is_not_logged_as_a_visit(self) -> None:
        log = self.root / "state" / "quarterdeck" / "visits.jsonl"
        self.assertEqual(self.fetch(self.port, "/qd/")[0], 200)
        visits = len(log.read_text().splitlines())
        status, page, headers = self.fetch(self.port, "/qd/index.html?lists=1")
        self.assertEqual(status, 200)
        self.assertIn('id="topics-unread"', page)
        self.assertEqual(headers.get("Content-Security-Policy"), "frame-ancestors 'none'")
        self.assertEqual(len(log.read_text().splitlines()), visits)
        self.fetch(self.port, "/qd/index.html")
        self.assertEqual(len(log.read_text().splitlines()), visits + 1)

    def test_the_version_marker_is_small_changes_with_the_lists_and_is_not_a_visit(self) -> None:
        log = self.root / "state" / "quarterdeck" / "visits.jsonl"
        self.assertEqual(self.fetch(self.port, "/qd/")[0], 200)
        visits = len(log.read_text().splitlines())
        status, body, headers = self.fetch(self.port, "/qd/index.html?version=1")
        self.assertEqual(status, 200)
        first = json.loads(body)
        self.assertEqual(list(first), ["version"])
        self.assertLess(len(body), 100)
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertEqual(json.loads(self.fetch(self.port, "/qd/index.html?version=1")[1]), first)
        self.assertIn(f'data-version="{first["version"]}"', self.fetch(self.port, "/qd/")[1])
        self.firstmate("post", "Q1", "--text", "The west dock is ready too.")
        self.assertNotEqual(json.loads(self.fetch(self.port, "/qd/index.html?version=1")[1]), first)
        self.assertEqual(len(log.read_text().splitlines()), visits + 1)  # only the page itself above

    def test_an_item_page_may_be_framed_by_quarterdeck_alone(self) -> None:
        status, _, headers = self.fetch(self.port, "/qd/q1.html?from=conversations")
        self.assertEqual((status, headers.get("Content-Security-Policy")), (200, "frame-ancestors 'self'"))


class MailboxBrowserTests(MailboxCase):
    def run_in_desk(self, steps: str, *, window_size: str = "1440,900", route: str = "/qd/", budget: int = 5000) -> None:
        if not shutil.which("google-chrome"):
            self.skipTest("google-chrome is required for the mailbox browser tests")
        page = base.WorkersTests.instrumented_page(quarterdeck.list_page, HELPERS + steps)
        with patch.object(quarterdeck, "list_page", page):
            outcome, stderr = base.WorkersTests.browser_page(self, self.port, route, window_size=window_size, budget=budget)
        self.assertEqual(outcome, "PASS", stderr)

    def check_reference_pointer_gestures(self) -> None:
        chrome = shutil.which("google-chrome")
        if not chrome:
            self.skipTest("google-chrome is required for the mailbox browser tests")
        launch = "import os, sys; fd = int(sys.argv[1]); os.dup2(fd, 3); os.dup2(fd, 4); os.execv(sys.argv[2], sys.argv[2:])"
        connection, child = socket.socketpair()
        with connection, child, subprocess.Popen(
            [sys.executable, "-c", launch, str(child.fileno()), chrome, "--headless", "--disable-background-networking",
             "--no-first-run", "--remote-debugging-pipe", "--window-size=1000,900",
             f"--user-data-dir={self.root / 'drag-browser'}", "about:blank"],
            pass_fds=(child.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ) as browser:
            child.close()
            connection.settimeout(10)

            def messages():
                pending = b""
                while chunk := connection.recv(65536):
                    parts = (pending + chunk).split(b"\0")
                    pending = parts.pop()
                    yield from map(json.loads, parts)

            responses = messages()
            events = []
            session_id = None
            sequence = 0

            def command(method, **params):
                nonlocal sequence
                sequence += 1
                request = {"id": sequence, "method": method, "params": params}
                if session_id:
                    request["sessionId"] = session_id
                connection.sendall(json.dumps(request).encode() + b"\0")
                for response in responses:
                    if response.get("id") == sequence:
                        self.assertNotIn("error", response, response)
                        return response["result"]
                    events.append(response)
                self.fail(f"Chrome closed its debugging pipe during {method}")

            def wait_for_event(method, matches):
                for event in chain(events, responses):
                    if event.get("method") == method and matches(event["params"]):
                        return event["params"]
                self.fail(f"Chrome closed its debugging pipe while waiting for {method}")

            def evaluate(expression):
                result = command("Runtime.evaluate", expression=expression, returnByValue=True)
                self.assertNotIn("exceptionDetails", result, result)
                return result["result"].get("value")

            def mouse(event_type, x, y, **params):
                command("Input.dispatchMouseEvent", type=event_type, x=x, y=y, **params)

            pane_display = "getComputedStyle(document.getElementById('pane-frame')).display"
            try:
                command("Target.setDiscoverTargets", discover=True)
                target = wait_for_event("Target.targetCreated", lambda event: event["targetInfo"]["type"] == "page")["targetInfo"]["targetId"]
                session_id = command("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
                command("Page.enable")
                command("Page.setLifecycleEventsEnabled", enabled=True)
                navigation = command("Page.navigate", url=f"http://127.0.0.1:{self.port}/qd/?tab=references")
                self.assertIn("loaderId", navigation, navigation)
                wait_for_event("Page.lifecycleEvent", lambda event: event["name"] == "load"
                               and event["frameId"] == navigation["frameId"] and event["loaderId"] == navigation["loaderId"])
                self.assertTrue(evaluate("!document.getElementById('desk-panel-references').hidden"))
                for selector, url in ((".bookmark-title", "https://example.com/tides"),
                                      (".notice-links a", "https://example.com/datum")):
                    x, y = evaluate(f"""(() => {{
  const link = document.querySelector('#topics-references [data-id="Q5"] {selector}');
  const box = link.getBoundingClientRect();
  window.referenceClick = null;
  document.addEventListener('click', event => {{
    window.referenceClick = [event.target.closest('a')?.href, !event.defaultPrevented];
    event.preventDefault();
  }}, {{once:true}});
  return [box.left + box.width / 2, box.top + box.height / 2];
}})()""")
                    mouse("mouseMoved", x, y)
                    mouse("mousePressed", x, y, button="left", buttons=1, clickCount=1)
                    mouse("mouseReleased", x, y, button="left", clickCount=1)
                    self.assertEqual(evaluate("window.referenceClick"), [url, True])
                    self.assertEqual(evaluate(pane_display), "none", url)
                for item_id, expected in (("Q5", "The official tables."), ("Q4", "What to check before the proof run.")):
                    start, end, y = evaluate(f"""(() => {{
  const text = document.querySelector('#topics-references [data-id="{item_id}"] .topic-latest');
  const range = document.createRange();
  range.selectNodeContents(text);
  const box = range.getBoundingClientRect();
  return [box.left + 1, box.right + 2, box.top + box.height / 2];
}})()""")
                    mouse("mouseMoved", start, y)
                    mouse("mousePressed", start, y, button="left", buttons=1, clickCount=1)
                    mouse("mouseMoved", (start + end) / 2, y, button="left", buttons=1)
                    mouse("mouseMoved", end, y, button="left", buttons=1)
                    mouse("mouseReleased", end, y, button="left", clickCount=1)
                    self.assertEqual(evaluate("window.getSelection().toString()"), expected, item_id)
                    self.assertEqual(evaluate(pane_display), "none", item_id)
            finally:
                browser.terminate()

    def setUp(self) -> None:
        super().setUp()
        self.port = self.start("/qd", ask_home=self.inbox())
        for title in ("Sail inventory", "Dock lights"):  # Q1, Q2: both unread
            self.firstmate("converse", "--title", title, "--text", f"Update on {title.lower()}.")
        self.questions("Pocket guide paper")  # Q3

    MOVE_STEPS = r"""
const choose = (id, value) => { const select = document.getElementById(id); select.value = value; select.dispatchEvent(new Event('change', {bubbles: true})); };
const visible = section => [...document.querySelectorAll('#' + section + ' .topic:not([hidden])')].map(row => row.dataset.id).join();
const place = async (title, project, topic, was = '') => {
  await until(() => pane()?.querySelector('h1')?.textContent === title && pane().querySelector('button.move'), title + ' did not open with Move');
  const page = pane();
  const form = page.getElementById('move-form');
  check(page.documentElement.classList.contains('in-pane') && form.hidden, 'The move form started open in the pane');
  page.querySelector('button.move').click();
  check(!form.hidden && page.getElementById('move-project').value === was, 'Move did not open on the current place');
  page.getElementById('move-project').value = project;
  page.getElementById('move-project').dispatchEvent(new Event('change', {bubbles: true}));
  if (topic) page.getElementById('move-topic').value = topic;
  form.requestSubmit();
  await until(() => form.hidden && page.querySelector('.state-status').textContent.startsWith(title + ' moved to '), title + ' was not moved');
};
"""

    def test_a_reference_and_a_question_set_are_moved_into_a_project_from_the_pane_and_show_under_its_filter(self) -> None:
        for name in ("rms cares", "quarterdeck"):
            self.assertEqual(self.post(self.port, "/qd/groups", {"name": name})[0], 200)
        self.add("resource", "Port tide tables", "The tables.", "--url", "https://example.com/tides", "--link-text", "Tide tables")  # Q4
        self.run_in_desk(self.MOVE_STEPS + r"""
document.getElementById('desk-tab-references').click();
document.querySelector('#topics-references .topic[data-id="Q4"] .bookmark-id').click();
await place('Port tide tables', 'P1', '');
await until(() => document.querySelector('#topics-references .topic[data-id="Q4"]')?.dataset.project === 'P1', 'The References row did not take the project from the live list');
choose('scope-project', 'P2');
check(visible('topics-references') === '' && document.getElementById('no-references').textContent
      === 'No references in quarterdeck yet. A reference you move into it shows here.', 'Another project kept the reference or gave no reason');
choose('scope-project', 'P1');
check(visible('topics-references') === 'Q4', 'The project did not show the moved reference');
choose('scope-project', '-');
check(visible('topics-references') === '', 'Ungrouped kept the moved reference');
choose('scope-project', '');
document.getElementById('desk-tab-needs-you').click();
document.querySelector('#review-waiting .topic[data-id="Q3"] .topic-title').click();
await place('Pocket guide paper', 'P2', '');
await until(() => document.querySelector('#review-waiting .topic[data-id="Q3"]')?.dataset.project === 'P2', 'The For review row did not take the project from the live list');
choose('scope-project', 'P2');
check(visible('review-waiting') === 'Q3', 'The project did not show the moved question set');
choose('scope-project', 'P1');
check(visible('review-waiting') === '', 'The wrong project showed the question set');
document.getElementById('desk-tab-references').click();
document.querySelector('#topics-references .topic[data-id="Q4"] .bookmark-id').click();
await place('Port tide tables', '', '', 'P1');
await until(() => !document.querySelector('#topics-references .topic[data-id="Q4"]')?.dataset.project, 'The References row kept the project it was moved out of');
""")
        placements = {item["id"]: item.get("placement") for item in self.store()["items"]}
        self.assertEqual((placements["Q3"], placements["Q4"]), ({"project": "P2"}, None))

    def test_a_row_opens_in_the_pane_on_a_wide_screen_is_read_and_keeps_its_place(self) -> None:
        self.run_in_desk(r"""
check(shown('mail-pane') && !shown('pane-frame') && shown('pane-empty'), 'The pane did not start empty beside the list');
check(count('conversations') === '2' && count('needs-you') === '1', 'The tabs did not count what is new');
document.querySelector('#topics-unread .topic[data-id="Q2"] .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Dock lights', 'The row did not open in the pane');
check(location.pathname.endsWith('/qd/') && shown('pane-frame') && !shown('pane-empty'), 'The Desk left its page or kept the empty line');
check(pane().documentElement.classList.contains('in-pane') && getComputedStyle(pane().querySelector('.bar')).display === 'none'
      && getComputedStyle(pane().querySelector('.back-link')).display === 'none', 'The item kept its site header or its Back link in the pane');
await until(() => count('conversations') === '1', 'Reading the reply did not lower the count');
const read = document.querySelector('#topics-unread .topic[data-id="Q2"]');
check(read && read.getAttribute('aria-current') === 'true' && read.dataset.unread === 'false',
      'The read row did not stay marked as open in its place under Unread');
pane().querySelector('.next-link').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Sail inventory', 'Next unread did not open the next conversation in the pane');
await until(() => document.querySelector('.topic[aria-current="true"]')?.dataset.id === 'Q1' && count('conversations') === '',
            'The list did not follow the pane to the next conversation');
check(!document.querySelector('#topics-unread .topic[data-id="Q2"]') && document.querySelector('#topics-recent .topic[data-id="Q2"]'),
      'The conversation that was read did not move on to Recent conversations');
check(!pane().querySelector('.next-link'), 'Next unread stayed with nothing left to read');
check(document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight,
      'The mailbox did not fit the window');
""")

    def after_q2_is_read(self, action):
        """Run an action from the command line once the open Desk has read Q2, as the owner's Firstmate would."""
        real = quarterdeck.desk_version
        done = []

        def version(data, underway):
            q2 = next(item for item in data["items"] if item["id"] == "Q2")
            if not done and not quarterdeck.unread_conversation(q2):
                done.append(1)
                action()
            return real(data, underway)
        return patch.object(quarterdeck, "desk_version", version)

    LIVE_STEPS = r"""
const checks = () => performance.getEntriesByName(new URL('index.html?version=1', location.href).href).length;
const lists = () => performance.getEntriesByName(new URL('index.html?lists=1', location.href).href).length;
const within = async (test, message) => {
  for (let waited = 0; waited < 40000; waited += 250) {
    if (test()) return;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(message);
};
"""

    def test_a_new_conversation_reaches_the_open_desk_without_a_reload(self) -> None:
        with self.after_q2_is_read(lambda: self.firstmate("converse", "--title", "Rope order", "--text", "The rope is on its way.")):
            self.run_in_desk(self.LIVE_STEPS + r"""
document.querySelector('#topics-unread .topic[data-id="Q2"] .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Dock lights', 'The row did not open in the pane');
const draft = document.getElementById('talk-message');
draft.value = 'A half written question';
await new Promise(resolve => setTimeout(resolve, 1000));
document.querySelector('#desk-tab-conversations').focus();
const marker = document.createElement('i');
document.body.append(marker);
const top = scrollY;
await within(() => ids('topics-unread').includes('Q4'), 'A new conversation never reached Unread');
check(count('conversations') === '2' && ids('topics-unread').split(',').sort().join() === 'Q1,Q2,Q4', 'The Unread list did not gain the conversation: ' + ids('topics-unread'));
check(count('needs-you') === '1', 'The For review count changed');
check(marker.isConnected, 'The Desk was reloaded');
check(document.querySelector('.topic[aria-current="true"]')?.dataset.id === 'Q2' && pane().querySelector('h1').textContent === 'Dock lights',
      'The open item changed');
check(scrollY === top, 'The scroll position moved');
check(draft.value === 'A half written question', 'The text being typed was lost');
check(document.activeElement === document.querySelector('#desk-tab-conversations'), 'Keyboard focus moved');
const quiet = lists();
await new Promise(resolve => setTimeout(resolve, 25000));
check(lists() === quiet, 'The lists were read again although nothing changed');
""", budget=60000)

    def test_the_open_conversation_picks_up_a_new_reply(self) -> None:
        with self.after_q2_is_read(lambda: self.firstmate("post", "Q2", "--text", "The west dock is ready too.")):
            self.run_in_desk(self.LIVE_STEPS + r"""
document.querySelector('#topics-unread .topic[data-id="Q2"] .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Dock lights', 'The row did not open in the pane');
const marker = document.createElement('i');
document.body.append(marker);
await within(() => pane()?.body?.textContent.includes('The west dock is ready too.'), 'The open conversation never showed the reply');
check(marker.isConnected, 'The Desk was reloaded');
check(document.querySelector('.topic[aria-current="true"]')?.dataset.id === 'Q2', 'The open item changed');
await within(() => count('conversations') === '2', 'The new reply did not count as unread');
""", budget=60000)

    def test_an_open_reply_box_with_text_keeps_the_pane_from_loading_again(self) -> None:
        with self.after_q2_is_read(lambda: self.firstmate("post", "Q2", "--text", "The west dock is ready too.")):
            self.run_in_desk(self.LIVE_STEPS + r"""
document.querySelector('#topics-unread .topic[data-id="Q2"] .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Dock lights', 'The row did not open in the pane');
const field = pane().querySelector('[data-conversation] textarea');
field.value = 'Half an answer';
field.focus();
const was = pane();
await new Promise(resolve => setTimeout(resolve, 35000));
check(pane() === was && pane().querySelector('[data-conversation] textarea').value === 'Half an answer', 'The pane loaded over typed text');
""", budget=60000)

    def test_a_phone_sized_desk_updates_the_same_way(self) -> None:
        real = quarterdeck.desk_version
        calls = []

        def version(data, underway):
            calls.append(1)
            if len(calls) == 2:
                self.firstmate("converse", "--title", "Rope order", "--text", "The rope is on its way.")
            return real(data, underway)
        with patch.object(quarterdeck, "desk_version", version):
            self.run_in_desk(self.LIVE_STEPS + r"""
check(count('conversations') === '2', 'The phone Desk did not start with two unread');
await within(() => count('conversations') === '3', 'The phone Desk did not gain the new conversation');
check(ids('topics-unread').split(',').includes('Q4'), 'The new conversation is not under Unread');
check(document.documentElement.scrollWidth <= innerWidth, 'The phone Desk scrolls sideways');
""", window_size="390,844", budget=60000)

    def test_no_check_runs_while_the_page_is_hidden_and_one_runs_on_return(self) -> None:
        self.run_in_desk(self.LIVE_STEPS + r"""
let hidden = true;
Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
await new Promise(resolve => setTimeout(resolve, 35000));
check(checks() === 0 && lists() === 0, 'The Desk checked while hidden');
hidden = false;
document.dispatchEvent(new Event('visibilitychange'));
await within(() => checks() === 1, 'The Desk did not check once on return');
""", budget=60000)

    def file_projects(self) -> None:
        """Q1 in rms cares (P1), Q2 in quarterdeck (P2), Q3 ungrouped, Q4 waiting for review in P1, and a reference Q5."""
        for name in ("rms cares", "quarterdeck"):
            self.assertEqual(self.post(self.port, "/qd/groups", {"name": name})[0], 200)
        self.firstmate("move", "Q1", "--project", "P1")
        self.firstmate("move", "Q2", "--project", "P2")
        self.firstmate("converse", "--title", "Rope plan", "--text", "Please look.", "--project", "P1", "--for-review")  # Q4
        self.add("resource", "Port tide tables", "The tables.", "--url", "https://example.com/tides", "--link-text", "Tide tables")  # Q5

    PROJECT_STEPS = r"""
const choose = value => { const select = document.getElementById('scope-project'); select.value = value; select.dispatchEvent(new Event('change', {bubbles: true})); };
const visible = section => [...document.querySelectorAll('#' + section + ' .topic:not([hidden])')].map(row => row.dataset.id).join();
const tabNamed = name => document.getElementById('desk-tab-' + name);
"""

    def test_a_project_narrows_conversations_for_review_and_references_with_their_counts_and_is_kept(self) -> None:
        self.file_projects()
        self.run_in_desk(self.PROJECT_STEPS + r"""
check(visible('topics-unread') === 'Q2,Q1' && count('conversations') === '2' && count('needs-you') === '2', 'All did not show everything');
check(getComputedStyle(document.getElementById('project-filter')).display !== 'none', 'The project choice is not on the Desk');
choose('P1');
check(visible('topics-unread') === 'Q1' && count('conversations') === '1', 'A project did not narrow Conversations and its count');
check(visible('review-waiting') === 'Q4' && count('needs-you') === '1', 'A project did not narrow For review and its count');
choose('P2');
check(visible('topics-unread') === 'Q2' && count('conversations') === '1' && count('needs-you') === '', 'The other project kept the first one\'s items or a zero count');
check(document.getElementById('no-review').textContent === 'Nothing in quarterdeck is waiting for your review.' && !document.getElementById('no-review').hidden, 'An empty For review gave no reason');
choose('-');
check(visible('topics-unread') === '' && count('conversations') === '' && visible('review-waiting') === 'Q3' && count('needs-you') === '1', 'Ungrouped did not show only items with no project');
tabNamed('references').click();
check(visible('topics-references') === 'Q5' && getComputedStyle(document.getElementById('project-filter')).display !== 'none', 'Ungrouped did not show the reference');
choose('P1');
check(visible('topics-references') === '' && !document.getElementById('no-references').hidden, 'A project kept a reference that is in no project');
tabNamed('under-way').click();
check(getComputedStyle(document.getElementById('project-filter')).display === 'none', 'The project choice shows on a tab it does not narrow');
check(JSON.parse(localStorage.getItem('quarterdeck-project')).project === 'P1', 'The choice was not kept in this browser');
""")
        self.run_in_desk(self.PROJECT_STEPS + r"""
check(document.getElementById('scope-project').value === 'P1', 'A reload lost the project');
check(visible('topics-unread') === 'Q1' && count('conversations') === '1' && count('needs-you') === '1', 'A reload showed other projects');
choose('');
check(visible('topics-unread') === 'Q2,Q1' && count('needs-you') === '2', 'All did not bring everything back');
""")

    def test_the_chosen_project_holds_when_the_live_list_brings_in_conversations(self) -> None:
        self.file_projects()
        calls = []
        real = quarterdeck.desk_version

        def version(data, underway):
            calls.append(1)
            if len(calls) == 2:
                self.firstmate("converse", "--title", "Rope order", "--text", "On its way.", "--project", "P2")  # Q6
            if len(calls) == 4:
                self.firstmate("converse", "--title", "Net repair", "--text", "Booked.", "--project", "P1")  # Q7
            return real(data, underway)
        with patch.object(quarterdeck, "desk_version", version):
            self.run_in_desk(self.LIVE_STEPS + self.PROJECT_STEPS + r"""
choose('P1');
await within(() => document.querySelector('.topic[data-id="Q6"]'), 'The other project\'s conversation never arrived');
check(document.getElementById('scope-project').value === 'P1' && visible('topics-unread') === 'Q1' && count('conversations') === '1',
      'A conversation in another project showed under the chosen one: ' + visible('topics-unread'));
await within(() => document.querySelector('.topic[data-id="Q7"]'), 'The chosen project\'s conversation never arrived');
check(visible('topics-unread') === 'Q7,Q1' && count('conversations') === '2' && count('needs-you') === '1', 'The new conversation did not join the chosen project');
choose('P2');
check(visible('topics-unread') === 'Q6,Q2' && count('conversations') === '2', 'The other project lost its new conversation');
""", budget=90000)

    def test_the_conversations_count_follows_only_the_project_and_not_tasks_only(self) -> None:
        self.file_projects()
        self.run_in_desk(self.PROJECT_STEPS + r"""
document.getElementById('scope-tasks').click();
check(visible('topics-unread') === '' && count('conversations') === '2', 'Tasks only lowered the unread count: ' + count('conversations'));
choose('P1');
check(count('conversations') === '1', 'The project did not narrow the unread count under Tasks only');
""")

    def test_arriving_after_an_answer_lands_on_the_next_item_the_project_shows(self) -> None:
        self.file_projects()
        self.run_in_desk(self.PROJECT_STEPS + "choose('-');")
        self.run_in_desk(self.PROJECT_STEPS + r"""
check(visible('review-waiting') === 'Q3', 'Ungrouped did not hold for the arrival');
check(document.activeElement?.closest('.topic')?.dataset.id === 'Q3', 'The arrival did not land on the next shown item');
""", route="/qd/?tab=needs-you&row=Q9")

    def test_a_phone_sized_desk_fits_the_project_choice(self) -> None:
        self.file_projects()
        self.run_in_desk(self.PROJECT_STEPS + r"""
choose('P1');
const filter = document.getElementById('project-filter').getBoundingClientRect();
check(filter.width > 0 && filter.right <= innerWidth, 'The project choice does not fit the phone');
check(document.documentElement.scrollWidth <= innerWidth, 'The phone Desk scrolls sideways');
""", window_size="390,844")

    def test_a_question_set_opens_in_the_pane_and_stays_open_when_deferred(self) -> None:
        self.questions("Harbor map first edition", TWO_QUESTIONS)  # Q4
        self.run_in_desk(r"""
document.getElementById('desk-tab-needs-you').click();
check(ids('review-waiting') === 'Q4,Q3' && !shown('review-later'), 'For review did not list what waits');
document.querySelector('#review-waiting .topic[data-id="Q4"] .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Harbor map first edition', 'The question set did not open in the pane');
pane().querySelector('button.later').click();
await until(() => ids('review-later') === 'Q4' && ids('review-waiting') === 'Q3' && count('needs-you') === '1',
            'The item did not move under Later and out of the count');
check(pane().querySelector('h1').textContent === 'Harbor map first edition', 'Deferring left the current item');
pane().querySelector('.next-link').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Pocket guide paper', 'Next unread did not open the next item');
check(document.querySelector('.topic[aria-current="true"]').dataset.id === 'Q3', 'The list did not mark the item now open');
""")

    def test_next_unread_visits_each_reply_before_leaving_the_conversation(self) -> None:
        self.firstmate("post", "Q2", "--text", "The west dock is ready too.")
        first, second = (f'reply-{reply["id"]}' for reply in self.item("Q2")["conversation"]["item"])
        self.run_in_desk(r"""
const [first, second] = """ + json.dumps([first, second]) + r""";
document.querySelector('#topics-unread .topic[data-id="Q2"] .topic-title').click();
await until(() => pane()?.activeElement?.id === first, 'The first unread reply did not open');
pane().querySelector('.next-link').click();
await until(() => pane()?.activeElement?.id === second && count('conversations') === '1',
            'Next unread did not read the second reply in the same conversation');
pane().querySelector('.list-nav.end .next-link').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Sail inventory' && count('conversations') === '',
            'Next unread did not continue to the next conversation');
""")

    def test_next_unread_tracks_scope_changes_without_reopening_the_item(self) -> None:
        self.run_in_desk(r"""
document.querySelector('#topics-unread .topic[data-id="Q2"] .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Dock lights', 'The conversation did not open');
const opened = pane();
check(opened.querySelectorAll('.next-link').length === 2, 'The unfiltered list did not offer the next reply');
document.getElementById('scope-tasks').click();
await until(() => opened.querySelector('.back-link').href.includes('tasks=1'), 'The pane did not adopt the current filter');
check(pane() === opened && !opened.querySelector('.next-link'), 'Next unread still offered a filtered-out conversation');
document.getElementById('scope-tasks').click();
await until(() => opened.querySelectorAll('.next-link').length === 2, 'Clearing the filter did not restore both Next unread links');
opened.querySelector('.next-link').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Sail inventory', 'The restored link did not open the next reply');
""")

    def test_an_item_that_loads_with_older_filters_adopts_the_current_ones(self) -> None:
        # The Desk's filters decide where Back and Next unread lead, also for a pane address rendered before they changed.
        self.run_in_desk(r"""
const errors = [];
addEventListener('error', event => errors.push(event.message));
document.getElementById('scope-unread').click();
frame.src = 'q2.html?from=conversations';
await until(() => pane()?.querySelector('h1')?.textContent === 'Dock lights', 'The conversation did not open');
await until(() => pane().querySelector('.back-link').href.includes('unread=1'), 'The pane kept the filters it loaded with');
const following = [...pane().querySelectorAll('.next-link')];
check(following.length === 2 && following.every(link => link.href.includes('unread=1')), 'Next unread did not follow the current filter');
check(!errors.length, 'The Desk raised an error while the item loaded: ' + errors[0]);
""")

    def test_pane_writes_update_hold_and_preserve_notice_acknowledgment(self) -> None:
        self.firstmate("catch-up", "add", "--title", "Tide chart published", "--summary", "The chart is ready.")
        self.run_in_desk(r"""
const send = window.fetch;
let refuse;
window.fetch = (path, ...args) => path === 'catch-ups/read'
  ? new Promise(resolve => { refuse = resolve; })
  : send(path, ...args);
const notice = () => document.querySelector('[data-read="C1"]');
notice().click();
check(notice().disabled, 'The acknowledgment control stayed enabled while saving');
document.querySelector('#topics-unread .topic[data-id="Q2"] .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Dock lights' && count('conversations') === '2',
            'Reading the reply did not refresh the Desk');
check(notice().disabled, 'Refreshing enabled the notice before the acknowledgment finished');
refuse(new Response('', {status:409}));
await until(() => notice().nextElementSibling.textContent.includes('Reload'), 'The notice lost its refusal guidance');
pane().querySelector('button.archive').click();
await until(() => ids('topics-archived') === 'Q2', 'The archive did not refresh the Desk');
check(notice().nextElementSibling.textContent.includes('Reload'), 'Archiving erased unrelated notice feedback');
document.getElementById('open-library').click();
document.getElementById('tab-conversations').click();
check(!document.querySelector('#conversations-list [data-id="Q2"]'), 'Hold still offered the archived conversation');
""")

    def test_a_narrow_screen_follows_the_link_to_the_items_own_page(self) -> None:
        self.run_in_desk(r"""
check(!shown('mail-pane'), 'A narrow screen showed the pane');
let prevented = null;
document.addEventListener('click', event => {
  if (!event.target.closest('a')) return;
  prevented = event.defaultPrevented;
  event.preventDefault();
});
document.querySelector('#topics-unread .topic-title').click();
check(prevented === false, 'A narrow screen did not leave the link to open its page');
document.getElementById('talk-open').click();
check(shown('mail-pane') && document.getElementById('talk-form').checkVisibility() &&
      !document.querySelector('.desk-tabs').checkVisibility() && document.activeElement.id === 'talk-topic',
      'A narrow screen did not give the composer its own view');
type('talk-topic', 'Keep this draft');
document.querySelector('.talk-back').click();
check(!shown('mail-pane') && document.querySelector('.desk-tabs').checkVisibility(), 'Back did not return to the list');
document.getElementById('talk-open').click();
check(document.getElementById('talk-topic').value === 'Keep this draft', 'Back cleared the unsent draft');
check(document.documentElement.scrollWidth <= innerWidth, 'The Desk scrolled sideways');
""", window_size="390,844")

    def test_a_new_conversation_is_written_in_the_pane_and_keeps_its_draft(self) -> None:
        self.run_in_desk(r"""
const form = document.getElementById('talk-form');
check(form.parentElement.id === 'pane-compose' && form.hidden && !shown('pane-compose'), 'The form did not wait in the pane');
document.querySelector('#topics-unread .topic-title').click();
await until(() => pane()?.querySelector('h1'), 'The row did not open');
document.getElementById('talk-open').click();
check(shown('pane-compose') && !shown('pane-frame') && document.activeElement.id === 'talk-topic',
      'New conversation did not show its blank message in the pane');
check(form.querySelector('.talk-to').textContent === 'To Firstmate', 'The message did not say who it is to');
type('talk-topic', 'Pier lighting');
type('talk-message', 'Can the east pier lights come on earlier?');
check(!form.querySelector('[type=submit]').disabled, 'A written message could not be started');
document.getElementById('talk-open').click();
check(form.hidden && shown('pane-frame'), 'Closing the composer did not return to the open item');
document.getElementById('talk-open').click();
check(document.getElementById('talk-topic').value === 'Pier lighting' &&
      document.getElementById('talk-message').value === 'Can the east pier lights come on earlier?', 'Closing cleared the draft');
form.requestSubmit();
await until(() => pane()?.querySelector('h1')?.textContent === 'Pier lighting', 'The started conversation did not open in the pane');
await until(() => document.querySelector('#topics-recent .topic')?.dataset.id === 'Q4', 'The started conversation did not join the list');
check(form.hidden && document.getElementById('desk-status').textContent === 'Message sent.'
      && document.querySelector('.topic[aria-current="true"]').dataset.id === 'Q4', 'The start was not confirmed or marked as open');
""")

    def test_unread_only_hides_read_conversations_and_says_when_none_is_unread(self) -> None:
        self.read("Q1")
        self.firstmate("catch-up", "add", "--title", "Ferry timetable is published", "--summary", "The winter timetable is out.")
        self.run_in_desk(r"""
const visible = () => [...document.querySelectorAll('#desk-panel-conversations .topic')].filter(row => !row.hidden).map(row => row.dataset.id || row.dataset.notice).join();
check(visible() === 'Q2,C1,Q1', 'The list did not start with every conversation: ' + visible());
document.getElementById('scope-unread').click();
check(visible() === 'Q2,C1' && !shown('topics-recent'), 'Unread only kept a read conversation');
check(new URL(document.querySelector('#topics-unread .topic-title').href).searchParams.get('unread') === '1',
      'A row did not carry the filter to its page');
check(JSON.parse(sessionStorage.getItem('quarterdeck-scope')).unread === true, 'The filter was not kept for Back');
document.querySelector('#topics-unread .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Dock lights', 'The unread conversation did not open');
await until(() => count('conversations') === '1', 'Reading it did not lower the count');
check(visible() === 'Q2,C1', 'The conversation just read left the filtered list before the owner moved on');
document.querySelector('[data-read="C1"]').click();
await until(() => count('conversations') === '', 'Marking the notice read did not clear the count');
document.getElementById('scope-unread').click();
document.querySelector('#topics-recent .topic[data-id="Q1"] .topic-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Sail inventory', 'A read conversation did not open');
await until(() => !document.querySelector('#topics-unread .topic'), 'The conversation that was read stayed under Unread after the owner moved on');
document.getElementById('scope-unread').click();
check(visible() === '' && shown('no-scoped-conversations')
      && document.getElementById('no-scoped-conversations').textContent === 'No conversation is unread. A reply you have not opened shows here.',
      'Unread only with nothing unread did not say so');
""")

    def test_a_reference_opens_in_the_pane_from_its_own_tab(self) -> None:
        self.add("list", "Proof run checklist", "What to check before the proof run.", "--body-file", "-",
                 stdin="- Confirm the paper\n- Sign off the legend\n")  # Q4
        self.add("resource", "Port tide tables", "The official tables.", "--url", "https://example.com/tides", "--link-text", "Tide tables")  # Q5
        self.run_in_desk(r"""
document.getElementById('desk-tab-references').click();
check(ids('topics-references') === 'Q5,Q4' && shown('mail-pane'), 'References did not list the saved links and lists beside the pane');
document.querySelector('#topics-references .topic[data-id="Q4"] .bookmark-title').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Proof run checklist', 'The list did not open in the pane');
check(document.querySelector('.topic[aria-current="true"]').dataset.id === 'Q4', 'The open reference was not marked in the list');
document.querySelector('#topics-references .topic[data-id="Q5"] .bookmark-id').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Port tide tables', 'The saved item did not open in the pane');
check(document.querySelector('#topics-references .topic[data-id="Q5"] .bookmark-title').href === 'https://example.com/tides',
      'The title of a saved link did not go to what it saves');
const destination = pane().querySelector('.open-link');
pane().addEventListener('click', event => event.preventDefault(), {once:true});
destination.click();
check(destination.target === '_blank', 'The saved destination would open inside the reading pane');
check(document.documentElement.scrollWidth <= innerWidth, 'The Desk scrolled sideways');
""")
        self.run_in_desk(r"""
check(document.getElementById('desk-tab-references').getAttribute('aria-selected') === 'true' && !document.getElementById('desk-panel-references').hidden,
      'Back did not open References');
check(document.activeElement === document.querySelector('#topics-references .topic[data-id="Q4"] .bookmark-title'), 'The row did not take focus');
check(document.documentElement.scrollWidth <= innerWidth, 'The Desk scrolled sideways');
""", window_size="390,844", route="/qd/?tab=references&row=Q4")

    def test_a_reference_row_opens_its_item_and_keeps_its_text_selectable(self) -> None:
        self.add("list", "Proof run checklist", "What to check before the proof run.", "--body-file", "-",
                 stdin="- Confirm the paper\n")  # Q4
        self.add("resource", "Port tide tables", "The official tables.", "--url", "https://example.com/tides", "--link-text", "Tide tables",
                 "--url", "https://example.com/datum", "--link-text", "Datum notes")  # Q5
        self.run_in_desk(r"""
document.getElementById('desk-tab-references').click();
const row = id => document.querySelector('#topics-references .topic[data-id="' + id + '"]');
const hit = (id, selector) => {
  const box = row(id).querySelector(selector).getBoundingClientRect();
  return document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
};
hit('Q5', '.topic-latest').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Port tide tables', 'Clicking the description did not open the saved item');
check(row('Q5').getAttribute('aria-current') === 'true', 'The open reference was not marked');
hit('Q4', '.topic-latest').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Proof run checklist', 'Clicking the description did not open the list');
hit('Q5', 'time').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Port tide tables', 'Clicking the date did not open the saved item');
hit('Q4', 'time').click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Proof run checklist', 'Clicking the date did not open the list');
const box = row('Q5').getBoundingClientRect();
document.elementFromPoint(box.right - 4, box.top + box.height / 2).click();
await until(() => pane()?.querySelector('h1')?.textContent === 'Port tide tables', 'Clicking the row whitespace did not open the saved item');
for (const link of [...row('Q5').querySelectorAll('a'), row('Q4').querySelector('.bookmark-title')]) {
  link.focus();
  check(document.activeElement === link && link.matches(':focus-visible') && getComputedStyle(link).outlineStyle !== 'none',
        'The focused link had no individual focus indicator: ' + link.textContent);
}
""", window_size="1000,900")
        self.check_reference_pointer_gestures()
        item_page = base.WorkersTests.instrumented_page(quarterdeck.item_page, r"""
check(window === window.top && location.pathname === '/qd/q5.html' && location.search === '?from=desk-references',
      'The saved item did not open as the top-level page');
check(document.querySelector('h1')?.textContent === 'Port tide tables', 'The phone opened the wrong item');
""")
        with patch.object(quarterdeck, "item_page", item_page):
            self.run_in_desk(r"""
document.getElementById('desk-tab-references').click();
check(document.documentElement.scrollWidth <= innerWidth, 'The Desk scrolled sideways');
const box = document.querySelector('#topics-references .topic[data-id="Q5"] .topic-latest').getBoundingClientRect();
document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2).click();
await quiet();
throw new Error('The phone stayed on the Desk');
""", window_size="390,844")

    def test_under_way_keeps_the_reading_pane_beside_the_list(self) -> None:
        self.run_in_desk(r"""
const width = document.getElementById('mail-list').getBoundingClientRect().width;
document.getElementById('desk-tab-under-way').click();
check(shown('desk-panel-under-way') && shown('mail-pane'), 'Under way lost the pane');
check(document.getElementById('mail-list').getBoundingClientRect().width === width, 'Switching tabs moved the list');
""")


HELPERS = r"""
const frame = document.getElementById('pane-frame');
const pane = () => { try { return frame.contentDocument; } catch { return null; } };
const shown = id => getComputedStyle(document.getElementById(id)).display !== 'none';
const count = tab => document.querySelector('#desk-tab-' + tab + ' .tab-count').textContent;
const ids = section => [...document.querySelectorAll('#' + section + ' .topic')].map(row => row.dataset.id).join();
const type = (id, text) => { const field = document.getElementById(id); field.value = text; field.dispatchEvent(new Event('input', {bubbles:true})); };
const until = (test, message) => new Promise((resolve, reject) => {
  const started = Date.now();
  const timer = setInterval(() => {
    let done = false;
    try { done = test(); } catch {}
    if (done) { clearInterval(timer); resolve(); }
    else if (Date.now() - started > 4000) { clearInterval(timer); reject(new Error(message)); }
  }, 25);
});
"""


if __name__ == "__main__":
    import unittest
    unittest.main()
