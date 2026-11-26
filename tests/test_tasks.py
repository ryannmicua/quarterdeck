from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_quarterdeck as base  # noqa: E402

quarterdeck = base.quarterdeck


class TaskCase(base.ImageCase):
    """A task is a conversation with a task field: Draft, Started, In review, then Resolved."""

    start = base.ServeTests.start
    fetch = base.ServeTests.fetch
    post = base.ConversationTests.post
    inbox = base.ConversationTests.inbox

    def binding(self, port):
        return re.search(r'id="task-form"[^>]*data-home="([^"]+)"', self.fetch(port, "/qd/")[1])[1]

    def create(self, port, action="start", title="Plan the ferry timetable", description="Draft next season's ferry timetable.",
               **extra):
        body = {"id": uuid.uuid4().hex, "title": title, "description": description, "action": action,
                "home": self.binding(port), **extra}
        return body, self.post(port, "/qd/tasks", body)

    def task(self, item="Q1"):
        return next(i for i in self.store()["items"] if i["id"] == item)

    def notes(self, home):
        path = home / "notes"
        return path.read_text() if path.exists() else ""

    def converse(self, title="Harbor brainstorm"):
        code, out, err = self.run_cli("converse", "--title", title, "--text", "What should the harbor guide cover?")
        self.assertEqual(code, 0, err)
        return out.strip()

    def row(self, page, item):
        return re.search(rf'<li class="topic" data-id="{item}".*?</li>', page, re.DOTALL)[0]

    def needs_you(self, port):
        page = self.fetch(port, "/qd/")[1]
        return re.search(r'id="desk-panel-needs-you".*?</section>', page, re.DOTALL)[0]


class CreateTaskTests(TaskCase):
    def test_start_saves_a_started_task_and_sends_the_fixed_message_with_the_description(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        body, (status, result) = self.create(port)
        self.assertEqual((status, result["message"]), (200, "Task started."))
        self.assertEqual(result["href"], "q1.html")
        item = self.task()
        self.assertEqual(item["task"], {"state": "started", "description": "Draft next season's ferry timetable."})
        (message,) = item["conversation"]["item"]
        self.assertEqual((message["author"], message["text"], message["delivery"]), ("you", "Task started.", "sent"))
        self.assertEqual(self.notes(home), f"CONVERSATION Q1 {message['id']} Topic: Plan the ferry timetable\n"
                                           "Task started.\n\nDescription:\nDraft next season's ferry timetable.\n")
        self.assertIn('data-task="true"', result["row"])
        self.assertIn('<span class="task-mark">Task</span>', result["row"])
        self.assertIn("<span>Started</span>", result["row"])
        self.assertIn("Draft next season", result["row"])
        self.assertIsNone(result["attention"])

    def test_starting_again_with_the_same_id_reuses_the_task_and_sends_once(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        body, _ = self.create(port)
        self.assertEqual(self.post(port, "/qd/tasks", body)[0], 200)
        self.assertEqual(len(self.store()["items"]), 1)
        self.assertEqual(self.notes(home).count("CONVERSATION Q1"), 1)
        self.assertEqual(self.post(port, "/qd/tasks", {**body, "description": "Something else."})[0], 409)

    def test_an_undelivered_start_keeps_the_task_and_the_page_offers_the_retry(self) -> None:
        port = self.start("/qd", ask_home=self.inbox(accept=False))
        _, (status, result) = self.create(port)
        self.assertEqual((status, result["message"]), (200, "Task saved. Not delivered."))
        self.assertIn("Not delivered", result["row"])
        self.assertIn("data-retry-ask", self.fetch(port, "/qd/q1.html")[1])

    def test_a_draft_saves_the_task_without_a_message_and_tells_firstmate_nothing(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        _, (status, result) = self.create(port, "draft")
        self.assertEqual((status, result["message"]), (200, "Draft saved."))
        item = self.task()
        self.assertEqual((item["task"]["state"], item["conversation"]), ("draft", {"item": []}))
        self.assertEqual(self.notes(home), "")
        self.assertIn("<span>Draft</span>", result["row"])
        self.assertIsNone(result["attention"])
        quarterdeck.check_store(self.store())
        self.assertIn("Task: Draft", self.run_cli("show", "Q1")[1])

    def test_saving_a_draft_again_with_the_same_id_returns_the_saved_draft(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        body, _ = self.create(port, "draft")
        status, result = self.post(port, "/qd/tasks", body)
        self.assertEqual((status, result["message"], result["href"]), (200, "Draft saved.", "q1.html"))
        self.assertEqual(len(self.store()["items"]), 1)
        self.assertEqual(self.post(port, "/qd/tasks", {**body, "title": "Something else"})[0], 409)
        self.assertEqual(self.create(port, "draft")[1][0], 200)
        self.assertEqual(len(self.store()["items"]), 2)

    def test_a_draft_page_offers_start_and_no_composer_then_start_begins_the_task(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        self.create(port, "draft")
        page = self.fetch(port, "/qd/q1.html")[1]
        self.assertIn('<button type="button" class="primary" id="start-task">Start</button>', page)
        self.assertIn("<h2 id=\"task-description-title\">Description</h2>", page)
        self.assertIn("Draft next season", page)
        self.assertNotIn('id="send-message"', page)
        self.assertNotIn('class="work item-conversation conversation"', page)
        home_binding = re.search(r'class="actions task-start" data-id="Q1" data-home="([^"]+)"', page)[1]
        start = {"part": "Q1", "id": uuid.uuid4().hex, "action": "start", "home": home_binding}
        status, result = self.post(port, "/qd/tasks", start)
        self.assertEqual((status, result["message"]), (200, "Task started."))
        self.assertEqual(self.task()["task"]["state"], "started")
        (message,) = self.task()["conversation"]["item"]
        self.assertEqual(message["id"], start["id"])
        self.assertIn("Description:\nDraft next season's ferry timetable.", self.notes(home))
        self.assertEqual(self.post(port, "/qd/tasks", start)[0], 200)
        self.assertEqual(self.notes(home).count("CONVERSATION Q1"), 1)
        self.assertEqual(self.post(port, "/qd/tasks", {**start, "id": uuid.uuid4().hex})[0], 409)
        page = self.fetch(port, "/qd/q1.html")[1]
        self.assertNotIn('id="start-task"', page)
        self.assertIn('id="send-message"', page)

    def test_a_task_created_from_a_conversation_is_related_to_it_in_both_directions(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        source = self.converse()
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            data["projects"] = [{"id": "P1", "name": "Field Atlas", "topics": [{"id": "T1", "name": "Wayfinding"}]}]
            data["items"][0]["placement"] = {"project": "P1", "topic": "T1"}
        _, (status, _) = self.create(port, source=source, title="Write the harbor guide")
        self.assertEqual(status, 200)
        task = self.task("Q2")
        self.assertEqual(task["task"]["source"], "Q1")
        self.assertEqual(task["placement"], {"project": "P1", "topic": "T1"})
        self.assertNotIn("task", self.task("Q1"))
        task_page = self.fetch(port, "/qd/q2.html")[1]
        self.assertIn('<h2 id="related-source">Related conversation</h2>', task_page)
        self.assertIn('<a href="q1.html"><span class="id">Q1</span> Harbor brainstorm</a>', task_page)
        source_page = self.fetch(port, "/qd/q1.html")[1]
        self.assertIn('<h2 id="related-tasks">Related tasks</h2>', source_page)
        self.assertRegex(source_page, r'<a href="q2.html"><span class="id">Q2</span> Write the harbor guide</a> <span class="meta">Started</span>')
        for page in (task_page, source_page):
            self.assertNotIn("Started from", page)
            self.assertNotIn("Tasks started here", page)
        self.assertEqual(self.create(port, source="Q9")[1][0], 400)
        code, _, err = self.run_cli("remove", "Q1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.task("Q2")["task"]["source"], "Q1")
        code, _, err = self.run_cli("show", "Q2")
        self.assertEqual(code, 0, err)
        status, task_page = self.fetch(port, "/qd/q2.html")[:2]
        self.assertEqual(status, 200)
        self.assertNotIn("Related conversation", task_page)
        self.assertEqual(self.create(port, source="q1")[1][0], 400)

    def test_a_task_created_on_the_desk_takes_the_chosen_placement(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.post(port, "/qd/groups", {"name": "Field Atlas"})
        self.assertEqual(self.create(port, "draft", project="P1")[1][0], 200)
        self.assertEqual(self.task()["placement"], {"project": "P1"})
        self.assertEqual(self.create(port, "draft", project="P7")[1][0], 400)

    def test_the_form_checks_its_fields_and_the_home(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        for change in ({"title": "  "}, {"description": ""}, {"action": "now"}, {"id": "nope"}, {"unknown": 1}):
            self.assertEqual(self.create(port, **change)[1][0], 400, change)
        self.assertEqual(self.create(port, home="0" * 64)[1][0], 409)
        self.assertFalse(quarterdeck.store_path().exists())

    def test_without_a_home_a_draft_can_be_saved_but_a_task_cannot_be_started(self) -> None:
        port = self.start("/qd")
        body = {"id": uuid.uuid4().hex, "title": "Plan", "description": "Plan it.", "action": "draft", "home": ""}
        self.assertEqual(self.post(port, "/qd/tasks", body)[0], 200)
        self.assertEqual(self.post(port, "/qd/tasks", {**body, "id": uuid.uuid4().hex, "action": "start"})[0], 403)
        self.assertEqual(len(self.store()["items"]), 1)
        self.assertEqual(self.post(port, "/qd/tasks", {"part": "Q1", "id": uuid.uuid4().hex, "action": "start", "home": ""})[0], 403)

    def test_the_desk_and_a_conversation_offer_create_a_task(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        desk = self.fetch(port, "/qd/")[1]
        self.assertIn('id="task-open"', desk)
        self.assertRegex(desk, r'<button type="button" class="primary" data-action="start" disabled>Start</button>'
                               r'<button type="button" data-action="draft" disabled>Save draft</button>')
        self.assertIn('<label for="task-title">Title</label>', desk)
        self.assertIn('<label for="task-description">Description</label>', desk)
        self.converse()
        page = self.fetch(port, "/qd/q1.html")[1]
        self.assertIn('id="task-form"', page)
        self.assertIn('data-source="Q1"', page)
        self.assertNotIn('id="task-open"', self.fetch(self.start("/qd"), "/qd/q1.html")[1])


class ReviewTaskTests(TaskCase):
    def started(self, port):
        self.create(port, title="Write the harbor guide")

    def ask_review(self, *extra):
        code, out, err = self.run_cli("task", "review", "q1", "--text", "The guide is ready to read.", *extra)
        self.assertEqual((code, err), (0, ""), err)
        return out

    def test_review_puts_the_task_in_needs_you_with_its_result_and_two_actions(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        self.started(port)
        self.assertNotIn("Approve", self.needs_you(port))
        self.assertEqual(self.ask_review("--url", "https://example.com/guide", "--link-text", "Harbor guide"), "Q1 In review\n")
        task = self.task()
        self.assertEqual(task["task"]["state"], "review")
        self.assertEqual(task["task"]["result"], {"text": "Harbor guide", "url": "https://example.com/guide"})
        last = task["conversation"]["item"][-1]
        self.assertEqual((last["author"], last["text"], last["links"]),
                         ("firstmate", "The guide is ready to read.", [{"text": "Harbor guide", "url": "https://example.com/guide"}]))
        panel = self.needs_you(port)
        self.assertIn('<a class="ref-link" href="https://example.com/guide" target="_blank" rel="noopener noreferrer">Harbor guide</a>', panel)
        self.assertIn('<button type="button" class="approve">Approve</button>', panel)
        self.assertIn('class="changes" aria-expanded="false">Changes needed</button>', panel)
        self.assertRegex(self.row(self.fetch(port, "/qd/")[1], "Q1"), r'<span class="task-mark">Task</span>.*<span class="badge">Unread reply</span>')
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            quarterdeck.read_firstmate_updates(data["items"][0])
        self.assertIn('<span>In review</span>', self.row(self.fetch(port, "/qd/")[1], "Q1"))
        self.assertIn("Approve", self.needs_you(port))
        page = self.fetch(port, "/qd/q1.html")[1]
        self.assertIn('<h2 id="review-title">Result</h2>', page)
        self.assertIn('<button type="button" class="approve primary">Approve</button>', page)
        self.assertIn('<span class="task-state">In review</span>', page)

    def test_the_result_link_is_optional_and_named_result_by_default(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.started(port)
        self.ask_review("--url", "https://example.com/guide")
        self.assertEqual(self.task()["task"]["result"]["text"], "Result")
        self.ask_review()
        self.assertNotIn("result", self.task()["task"])

    def test_review_is_refused_for_what_is_not_a_started_task(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.assertNotEqual(self.run_cli("task", "review", "Q1", "--text", "Done.")[0], 0)
        self.converse()
        code, _, err = self.run_cli("task", "review", "Q1", "--text", "Done.")
        self.assertIn("use a task", err)
        self.create(port, "draft")
        code, _, err = self.run_cli("task", "review", "Q2", "--text", "Done.")
        self.assertIn("is a draft", err)
        self.run_cli("task", "review", "Q2", "--text", "Done.")
        self.assertEqual(self.task("Q2")["task"]["state"], "draft")
        self.create(port)
        self.run_cli("resolve", "Q3")
        self.assertIn("is resolved", self.run_cli("task", "review", "Q3", "--text", "Done.")[2])
        self.assertIn("--link-text needs --url", self.run_cli("task", "review", "Q3", "--text", "x", "--link-text", "y")[2])

    def test_approve_resolves_the_task_and_clears_the_review(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.started(port)
        self.ask_review("--url", "https://example.com/guide")
        status, result = self.post(port, "/qd/reviews", {"part": "Q1", "decision": "approve"})
        self.assertEqual((status, result["message"], result["attention"]), (200, "Task approved.", None))
        task = self.task()
        self.assertEqual(task["task"], {"state": "started", "description": "Draft next season's ferry timetable."})
        self.assertIs(task["resolved"], True)
        self.assertNotIn("Approve", self.needs_you(port))
        self.assertIn('data-resolved="true"', result["row"])
        self.assertEqual(self.post(port, "/qd/reviews", {"part": "Q1", "decision": "approve"})[0], 409)
        quarterdeck.check_store(self.store())

    def test_resolving_from_the_command_line_ends_the_review_too(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.started(port)
        self.ask_review()
        self.assertIn("Approve", self.needs_you(port))
        self.run_cli("resolve", "Q1")
        self.assertNotIn("Approve", self.needs_you(port))
        self.assertIn('<span class="task-state" hidden>', self.fetch(port, "/qd/q1.html")[1])

    def test_changes_needed_sends_the_note_and_returns_the_task_to_started(self) -> None:
        home = self.inbox()
        port = self.start("/qd", ask_home=home)
        self.started(port)
        self.ask_review("--url", "https://example.com/guide")
        binding = re.search(r'class="task-review" data-id="Q1" data-home="([^"]+)"', self.needs_you(port))[1]
        body = {"part": "Q1", "decision": "changes", "id": uuid.uuid4().hex, "note": "Add the ferry times.", "home": binding}
        status, result = self.post(port, "/qd/reviews", body)
        self.assertEqual((status, result["message"], result["attention"]), (200, "Note sent.", None))
        task = self.task()
        self.assertEqual(task["task"]["state"], "started")
        self.assertNotIn("result", task["task"])
        self.assertNotIn("resolved", task)
        last = task["conversation"]["item"][-1]
        self.assertEqual((last["id"], last["author"], last["text"], last["delivery"]),
                         (body["id"], "you", "Changes needed.\n\nAdd the ferry times.", "sent"))
        self.assertIn(f"CONVERSATION Q1 {body['id']} Topic: Write the harbor guide\nChanges needed.\n\nAdd the ferry times.", self.notes(home))
        self.assertNotIn("Approve", self.needs_you(port))
        self.assertEqual(self.post(port, "/qd/reviews", body)[0], 200)
        self.assertEqual(self.notes(home).count("Add the ferry times."), 1)
        self.assertEqual(self.post(port, "/qd/reviews", {**body, "id": uuid.uuid4().hex})[0], 409)
        self.assertEqual(self.post(port, "/qd/reviews", {**body, "note": "Other.", })[0], 409)

    def test_review_requests_check_their_fields(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.started(port)
        self.ask_review()
        binding = self.binding(port)
        changes = {"part": "Q1", "decision": "changes", "id": uuid.uuid4().hex, "note": "Fix.", "home": binding}
        for body in ({"part": "Q1", "decision": "maybe"}, {"part": "Q1", "decision": "approve", "note": "x"},
                     {**changes, "note": " "}, {k: v for k, v in changes.items() if k != "note"}, {**changes, "part": "q1"},
                     {**changes, "id": "nope"}):
            self.assertEqual(self.post(port, "/qd/reviews", body)[0], 400, body)
        self.assertEqual(self.post(port, "/qd/reviews", {**changes, "home": "0" * 64})[0], 409)
        self.assertEqual(self.task()["task"]["state"], "review")

    def test_without_a_home_a_review_can_be_approved_but_not_answered_with_a_note(self) -> None:
        port = self.start("/qd")
        self.assertEqual(self.post(port, "/qd/tasks", {"id": uuid.uuid4().hex, "title": "Guide", "description": "Write it.",
                                                        "action": "draft", "home": ""})[0], 200)
        self.assertEqual(self.post(port, "/qd/reviews", {"part": "Q1", "decision": "approve"})[0], 409)
        with quarterdeck.changing_store(quarterdeck.store_path()) as data:
            data["items"][0]["task"]["state"] = "review"
            data["items"][0]["conversation"]["item"].append(
                quarterdeck.firstmate_message("Done.", [], []))
        changes = {"part": "Q1", "decision": "changes", "id": uuid.uuid4().hex, "note": "Fix.", "home": ""}
        self.assertEqual(self.post(port, "/qd/reviews", changes)[0], 403)
        self.assertEqual(self.post(port, "/qd/reviews", {"part": "Q1", "decision": "approve"})[0], 200)


class TaskFilterTests(TaskCase):
    def test_tasks_only_filter_and_the_rows_it_hides(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.converse()
        self.create(port, title="Write the harbor guide")
        page = self.fetch(port, "/qd/")[1]
        self.assertIn('<div class="scope-check"><input type="checkbox" id="scope-tasks"><label for="scope-tasks">Tasks only</label></div>', page)
        self.assertNotIn("data-task", self.row(page, "Q1"))
        self.assertIn('data-task="true"', self.row(page, "Q2"))
        self.assertNotIn("task-mark", self.row(page, "Q1"))
        self.assertNotIn("<span>Started</span>", self.row(page, "Q1"))

    def test_next_unread_follows_the_filter(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.converse("First")
        self.create(port, title="Second")
        self.converse("Third")
        context = {"from": "conversations", "tasks": "1"}
        data = self.store()
        self.assertEqual([i["id"] for i in data["items"] if quarterdeck.in_desk_scope(data, context, i)], ["Q2"])
        self.assertEqual(quarterdeck.parse_list_context("from=conversations&tasks=1"), context)
        self.assertIn("tasks=1", quarterdeck.back_link_href(context, "Q2"))
        self.assertEqual([i["id"] for i in quarterdeck.list_members(data, context)], ["Q2"])


class LegacyStoreTests(TaskCase):
    def test_a_conversation_saved_before_tasks_needs_no_change_and_is_not_a_task(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.converse()
        before = self.store()
        quarterdeck.check_store(before)
        self.assertNotIn("task", before["items"][0])
        page = self.fetch(port, "/qd/q1.html")[1]
        self.assertNotIn('class="task-description"', page)
        self.assertNotIn('class="task-state"', page)
        self.assertIn('<p class="meta">Conversation', page)
        self.assertIn("Create a task", page)
        self.assertNotIn("Task:", self.run_cli("show", "Q1")[1])
        self.assertEqual(self.store(), before)

    def test_the_store_refuses_a_damaged_task(self) -> None:
        self.converse()
        self.add("resource", "Ferry times", "Saved.", "--url", "https://example.com/ferry", "--link-text", "Ferry")
        good = {"state": "started", "description": "Do it."}

        def damaged(item, task):
            data = self.store()
            data["items"][item]["task"] = task
            with self.assertRaises(ValueError) as caught:
                quarterdeck.check_store(data)
            return str(caught.exception)

        self.assertIn("only a conversation can be a task", damaged(1, good))
        for task in ({**good, "state": "done"}, {"state": "started"}, {**good, "extra": 1}, {**good, "draft_id": "ABC"}, {**good, "draft_id": 7},
                     {**good, "source": "Q1"}, {**good, "source": "q2"}, {**good, "result": {"text": "R", "url": "https://example.com"}},
                     {"state": "review", "description": "Do it.", "result": {"text": "R", "url": "ftp://example.com"}},
                     {**good, "description": " padded "}, {**good, "state": ["started"]}, "started"):
            self.assertIn("damaged", damaged(0, task), task)
        data = self.store()
        data["items"][0]["conversation"]["item"] = []
        with self.assertRaises(ValueError):
            quarterdeck.check_store(data)
        data["items"][0]["task"] = {"state": "draft", "description": "Do it."}
        self.assertIs(quarterdeck.check_store(data), data)


class TaskBrowserTests(TaskCase):
    """The Desk and task pages in a real browser, with the server's answers replaced."""

    check_answer_page = base.ServeTests.check_answer_page

    def desk(self):
        port = self.start("/qd", ask_home=self.inbox())
        self.converse("Harbor brainstorm")
        self.create(port, "draft", title="Write the guide", description="Write a one page guide.")
        return port

    def test_tasks_only_and_the_create_form_on_the_desk(self) -> None:
        port = self.desk()
        row = self.row(self.fetch(port, "/qd/")[1], "Q2")
        self.check_answer_page(port, r"""
const posts = [];
window.fetch = async (path, init) => {
  posts.push([path, JSON.parse(init.body)]);
  return {ok:true, status:200, json: async () => ({message:'Draft saved.', href:'q2.html', row:%s, hold:'', attention:null})};
};
const settle = () => new Promise(resolve => setTimeout(resolve, 80));
const shown = () => [...document.querySelectorAll('#desk-panel-conversations li.topic')].filter(r => !r.hidden).map(r => r.dataset.id).sort().join();
const fill = (id, text) => { button(id).value = text; button(id).dispatchEvent(new Event('input', {bubbles:true})); };
check(shown() === 'Q1,Q2', 'The Desk did not list both conversations');
button('scope-tasks').click();
check(shown() === 'Q2', 'Tasks only did not hide the conversation that is not a task');
check(document.querySelector('li.topic[data-id="Q2"] .topic-title').getAttribute('href').includes('tasks=1'),
      'The task link did not carry the filter');
button('scope-tasks').click();
check(shown() === 'Q1,Q2', 'Clearing Tasks only did not show every conversation');
button('task-open').click();
check(!button('task-form').hidden && document.activeElement === button('task-title'), 'Create a task did not open the form at its title');
const [start, draft] = button('task-form').querySelectorAll('button');
check(start.disabled && draft.disabled, 'The buttons were enabled before the fields were filled');
fill('task-title', '  Write   the guide ');
check(start.disabled, 'The buttons were enabled with no Description');
fill('task-description', 'Write a one page guide.');
check(!start.disabled && !draft.disabled && start.textContent === 'Start' && draft.textContent === 'Save draft', 'The buttons did not enable');
button('task-title').dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
check(button('task-form').hidden && document.activeElement === button('task-open'), 'Escape did not close the form');
check(button('task-title').value !== '', 'Escape lost the draft text');
button('task-open').click();
draft.click();
await settle();
const [path, body] = posts[0];
check(posts.length === 1 && path === 'tasks' && body.action === 'draft' && body.title === 'Write the guide'
      && body.description === 'Write a one page guide.' && /^[0-9a-f]{32}$/.test(body.id) && !('source' in body),
      'Save draft posted the wrong request: ' + JSON.stringify(posts));
check(button('task-form').hidden && button('task-title').value === '' && button('desk-status').textContent === 'Draft saved.',
      'A saved draft did not clear and close the form');
check(shown() === 'Q1,Q2', 'The saved task row went missing');
fill('task-title', 'Second');
fill('task-description', 'Another.');
button('task-form').querySelector('[data-action="start"]').click();
await settle();
check(posts.length === 2 && posts[1][1].action === 'start' && posts[1][1].id !== body.id, 'Start posted the wrong request');
""" % json.dumps(row), path="/qd/", viewport=(390, 844))

    def test_tasks_only_with_no_tasks_says_what_will_fill_it(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.converse("Harbor brainstorm")
        self.check_answer_page(port, r"""
const none = button('no-scoped-conversations');
check(none.hidden, 'The empty message showed while conversations were listed');
button('scope-tasks').click();
check(!none.hidden && none.textContent === 'No tasks yet. A task you create shows here.', 'Tasks only with no tasks did not explain itself: ' + none.textContent);
check(button('topics-recent').hidden, 'The recent group stayed visible with no task in it');
button('scope-tasks').click();
check(none.hidden && !button('topics-recent').hidden, 'Clearing the filter did not bring the list back');
""", path="/qd/", viewport=(390, 844))

    def test_a_review_is_decided_from_needs_you(self) -> None:
        port = self.start("/qd", ask_home=self.inbox())
        self.create(port, title="Write the guide")
        self.assertEqual(self.run_cli("task", "review", "Q1", "--text", "Ready.", "--url", "https://example.com/guide")[0], 0)
        row = self.row(self.fetch(port, "/qd/")[1], "Q1")
        self.check_answer_page(port, r"""
button('desk-tab-needs-you').click();
const posts = [];
let refuse = false;
window.fetch = async (path, init) => {
  posts.push([path, JSON.parse(init.body)]);
  if (refuse) throw new Error('interrupted');
  return {ok:true, status:200, json: async () => ({message:'Task approved.', href:'q1.html', row:%s, hold:'', attention:null})};
};
const settle = () => new Promise(resolve => setTimeout(resolve, 80));
const rows = () => document.querySelectorAll('#desk-panel-needs-you tr.work').length;
const box = document.querySelector('.task-review');
check(rows() === 1 && box.querySelector('.approve').textContent === 'Approve'
      && box.querySelector('.changes').textContent === 'Changes needed', 'Needs you did not offer both decisions');
check(box.closest('tr').querySelector('.ref-link').href === 'https://example.com/guide', 'The result link was missing');
const note = box.querySelector('.review-note');
check(note.hidden && box.querySelector('.changes').getAttribute('aria-expanded') === 'false', 'The note started open');
box.querySelector('.changes').click();
check(!note.hidden && document.activeElement === note.querySelector('textarea')
      && box.querySelector('.changes').getAttribute('aria-expanded') === 'true', 'Changes needed did not open the note');
check(note.querySelector('.send-note').disabled, 'Send note was enabled with no note');
note.querySelector('textarea').dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
check(note.hidden && document.activeElement === box.querySelector('.changes'), 'Escape did not close the note');
box.querySelector('.changes').click();
refuse = true;
note.querySelector('textarea').value = '  Add the Monday rows. ';
note.querySelector('textarea').dispatchEvent(new Event('input', {bubbles:true}));
note.querySelector('.send-note').click();
await settle();
const [path, body] = posts[0];
check(path === 'reviews' && body.part === 'Q1' && body.decision === 'changes' && body.note === 'Add the Monday rows.'
      && /^[0-9a-f]{32}$/.test(body.id) && /^[0-9a-f]{64}$/.test(body.home), 'Send note posted the wrong request: ' + JSON.stringify(posts));
check(rows() === 1 && /could not be saved|Offline/.test(box.querySelector('.review-status').textContent)
      && !note.querySelector('.send-note').disabled, 'An interrupted note changed the row or hid the reason');
refuse = false;
note.querySelector('.send-note').click();
await settle();
check(posts[1][1].id === body.id, 'Sending an interrupted note again used a new message ID');
check(rows() === 0 && document.getElementById('desk-status').textContent === 'Task approved.', 'A saved review did not leave Needs you');
check(document.querySelector('.tab-count').hidden, 'The Needs you count stayed');
""" % json.dumps(row), path="/qd/", viewport=(390, 844))

    def test_a_task_page_starts_a_draft_and_creates_a_related_task(self) -> None:
        port = self.desk()
        self.check_answer_page(port, r"""
const posts = [];
window.fetch = async (path, init) => {
  posts.push([path, JSON.parse(init.body)]);
  return {ok:false, status:409, json: async () => ({message:'This task is already started. Reload the page.', saved:false})};
};
const settle = () => new Promise(resolve => setTimeout(resolve, 80));
const output = document.querySelector('.task-start output');
button('start-task').click();
await settle();
check(posts.length === 1 && posts[0][0] === 'tasks' && posts[0][1].part === 'Q2' && posts[0][1].action === 'start'
      && /^[0-9a-f]{32}$/.test(posts[0][1].id) && /^[0-9a-f]{64}$/.test(posts[0][1].home), 'Start posted the wrong request');
check(output.textContent === 'This task is already started. Reload the page.' && !button('start-task').disabled, 'The refusal was not shown beside Start');
button('task-open').click();
button('task-title').value = 'Follow up';
button('task-title').dispatchEvent(new Event('input', {bubbles:true}));
button('task-description').value = 'Do the follow up.';
button('task-description').dispatchEvent(new Event('input', {bubbles:true}));
button('task-form').querySelector('[data-action="draft"]').click();
await settle();
check(posts[1][1].source === 'Q2' && posts[1][1].action === 'draft', 'A task created on a page was not related to it');
check(button('task-form').querySelector('.task-status').textContent === 'This task is already started. Reload the page.'
      && !button('task-form').hidden, 'The form closed on a refusal');
""", path="/qd/q2.html", viewport=(390, 844))


if __name__ == "__main__":
    import unittest
    unittest.main()
