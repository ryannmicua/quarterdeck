from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_quarterdeck as base  # noqa: E402

quarterdeck = base.quarterdeck
snapshot_task = base.snapshot_task
SNAPSHOT_TIME = base.SNAPSHOT_TIME
READING_DAY = datetime(2026, 3, 2, 13, 40, tzinfo=timezone.utc).astimezone().date().isoformat()
PULL = "https://example.com/pull/"


def merged(key, date, *, title="Sanding the dock", repo="Copper Orchard", pr=PULL + "9", kind="ship"):
    return {"id": key, "state": "done", "kind": kind, "title": title, "repo": repo, "pr_url": pr,
            "completion": {"verb": "merged", "date": date}}


def reading(tasks, records=()):
    return json.dumps({"generated": SNAPSHOT_TIME, "tasks": tasks, "backlog": {"records": list(records)}})


class UnderwayTests(base.ImageCase):
    """The Under way tab reads only the saved workers reading and the optional task to conversation map."""

    start = base.ServeTests.start
    fetch = base.ServeTests.fetch
    post = base.ConversationTests.post
    inbox = base.ConversationTests.inbox
    begin = base.TalkToFirstmateTests.begin
    make_home = base.WorkersTests.make_home
    cache_file = base.WorkersTests.cache_file

    def work(self, tasks, records=()):
        home = self.make_home(body=reading(tasks, records))
        return {row["id"]: row for row in quarterdeck.workers_snapshot(home)["work"]}

    def panel(self, page):
        match = re.search(r'<section class="desk-panel" role="tabpanel" id="desk-panel-under-way".*?</section>', page, re.S)
        self.assertIsNotNone(match)
        return match[0]

    def served_panel(self, home, **options):
        port = self.start("/qd", workers_home=home, **options)
        self.assertEqual(self.fetch(port, "/qd/workers.json")[0], 200)  # one check fills the saved reading
        with patch.object(quarterdeck, "run_snapshot") as run_snapshot:
            status, page, _ = self.fetch(port, "/qd/")
        run_snapshot.assert_not_called()
        self.assertEqual(status, 200)
        return self.panel(page), port

    def test_each_status_word_comes_from_what_the_reading_states(self) -> None:
        rows = self.work([
            snapshot_task("Dock", title="Sanding the dock"),
            snapshot_task("Pier", pr=PULL + "1"),
            snapshot_task("Quay", state="done", pr=PULL + "2"),
            snapshot_task("Slip", state="done"),
            snapshot_task("Jetty", state="blocked", pr=PULL + "3"),
            snapshot_task("Wharf", decisions=["colour"], pr=PULL + "4"),
            snapshot_task("Mole", state="paused"),
            snapshot_task("Berth", freshness="stale", pr=PULL + "5"),
            snapshot_task("Buoy", kind="scout"),
            snapshot_task("Marker", kind="scout", pr=PULL + "6"),
            snapshot_task("Gone", state="done", exists=False, pr=PULL + "7"),
            snapshot_task("Mate", kind="secondmate"),
        ])
        self.assertEqual({key: row["status"] for key, row in rows.items()},
                         {"Dock": "Building", "Pier": "In review", "Quay": "Ready to merge", "Slip": "Finished",
                          "Jetty": "Stuck", "Wharf": "Question asked", "Mole": "Unknown", "Berth": "Unknown",
                          "Buoy": "Working", "Marker": "In review"})
        self.assertEqual(list(rows), ["Quay", "Pier", "Marker", "Dock", "Buoy", "Wharf", "Jetty", "Mole", "Berth", "Slip"])
        self.assertEqual(rows["Dock"], {"id": "Dock", "title": "Sanding the dock", "project": "Copper Orchard",
                                        "status": "Building", "pr": None, "merged_on": None})
        self.assertEqual(rows["Quay"]["pr"], PULL + "2")

    def test_work_merged_today_shows_whether_or_not_its_terminal_is_open(self) -> None:
        rows = self.work([snapshot_task("Open", state="done", pr=PULL + "1"), snapshot_task("Older", state="done", pr=PULL + "2"),
                          snapshot_task("Live")],
                         [merged("Open", READING_DAY, pr=PULL + "1"), merged("Older", "2026-02-27", pr=PULL + "2"),
                          merged("Closed", READING_DAY, title="Painting the hull", pr=PULL + "8"),
                          merged("Yesterday", "2026-03-01"), merged("Notes", READING_DAY, kind="program"),
                          {**merged("Reported", READING_DAY), "completion": {"verb": "reported", "date": READING_DAY}},
                          {**merged("Broken", READING_DAY), "completion": {"verb": "merged", "date": "soon"}},
                          "not a record", {"id": None}])
        self.assertEqual({key: row["status"] for key, row in rows.items()},
                         {"Live": "Building", "Open": "Merged today", "Closed": "Merged today"})
        self.assertEqual(rows["Closed"], {"id": "Closed", "title": "Painting the hull", "project": "Copper Orchard",
                                          "status": "Merged today", "pr": PULL + "8", "merged_on": READING_DAY})

    def test_a_reading_without_backlog_records_still_lists_the_work(self) -> None:
        home = self.make_home([snapshot_task("Dock")])
        self.assertEqual([row["status"] for row in quarterdeck.workers_snapshot(home)["work"]], ["Building"])
        for backlog in (None, [], {"records": "none"}):
            with self.subTest(backlog=backlog):
                home = self.make_home(body=json.dumps({"generated": SNAPSHOT_TIME, "tasks": [snapshot_task("Dock")],
                                                       "backlog": backlog}))
                self.assertEqual(len(quarterdeck.workers_snapshot(home)["work"]), 1)

    def test_the_tab_lists_rows_from_the_saved_reading_without_running_the_snapshot(self) -> None:
        home = self.make_home(body=reading([snapshot_task("Dock", title="Sanding the dock"),
                                            snapshot_task("Quay", title="Tarring the quay", state="done", pr=PULL + "2")]))
        panel, port = self.served_panel(home)
        self.assertIn('<th scope="col">Work</th>', panel)
        self.assertIn("Tarring the quay", panel)
        self.assertLess(panel.index("Tarring the quay"), panel.index("Sanding the dock"))
        self.assertIn(">Ready to merge</span>", panel)
        self.assertIn(">Building</span>", panel)
        self.assertIn(f'<a href="{PULL}2" rel="noopener noreferrer" aria-label="Pull request for Tarring the quay">', panel)
        self.assertEqual(panel.count("Pull request</a>"), 1)
        self.assertNotIn("Conversation</a>", panel)
        self.assertIn(f'Last checked <time datetime="{SNAPSHOT_TIME}">', panel)
        self.assertIn('id="desk-tab-under-way"', self.fetch(port, "/qd/")[1])

    def test_the_tab_is_empty_until_the_first_check_and_never_starts_one(self) -> None:
        home = self.make_home([snapshot_task("Dock")])
        port = self.start("/qd", workers_home=home)
        with patch.object(quarterdeck, "run_snapshot") as run_snapshot:
            page = self.fetch(port, "/qd/")[1]
        run_snapshot.assert_not_called()
        self.assertIn("Quarterdeck has not checked the workers yet.", self.panel(page))
        self.assertNotIn("Last checked", self.panel(page))

    def test_the_tab_says_when_no_work_is_under_way_or_no_home_is_set(self) -> None:
        panel, _ = self.served_panel(self.make_home([snapshot_task("Gone", exists=False)]))
        self.assertIn("No work is under way.", panel)
        self.assertNotIn("<table", panel)
        port = self.start("/qd")
        self.assertIn("once firstmate_home names a Firstmate home", self.panel(self.fetch(port, "/qd/")[1]))

    def test_a_failed_check_keeps_the_last_reading_and_says_so(self) -> None:
        home = self.make_home([snapshot_task("Dock", title="Sanding the dock")])
        port = self.start("/qd", workers_home=home)
        self.assertEqual(self.fetch(port, "/qd/workers.json")[0], 200)
        self.make_home(code=1)
        self.assertEqual(self.fetch(port, "/qd/workers.json")[0], 502)
        panel = self.panel(self.fetch(port, "/qd/")[1])
        self.assertIn("Sanding the dock", panel)
        self.assertIn(f'Check failed. Showing the reading from <time datetime="{SNAPSHOT_TIME}">', panel)

    def test_a_row_links_to_its_conversation_when_the_map_names_an_existing_one(self) -> None:
        home = self.make_home(body=reading([snapshot_task("Dock", title="Sanding the dock"),
                                            snapshot_task("Pier", title="Mending the pier"),
                                            snapshot_task("Slip", title="Oiling the slip"),
                                            snapshot_task("Wharf", title="Painting the wharf")]))
        (home / "data" / "qd-threads").mkdir(parents=True)
        (home / "data" / "qd-threads" / "links.tsv").write_text(
            "# task\titem\nDock\tQ9\nDock\tQ1\nPier\tQ7\nSlip\tnot an id\nWharf\tQ1\tQ3\nbroken line\n")
        port = self.start("/qd", workers_home=home, ask_home=self.inbox())
        self.assertEqual(self.begin(port, "Harbor lamp colours")[1][0], 200)
        self.assertEqual(self.fetch(port, "/qd/workers.json")[0], 200)
        panel = self.panel(self.fetch(port, "/qd/")[1])
        rows = dict(zip(("Dock", "Pier", "Slip", "Wharf"), re.findall(r"<tr>.*?</tr>", panel, re.S)[1:]))
        self.assertEqual(len(rows), 4)
        self.assertIn('<a href="q1.html" aria-label="Conversation for Sanding the dock">Conversation</a>', rows["Dock"])
        self.assertIn('<a href="q1.html" aria-label="Conversation for Painting the wharf">Conversation</a>', rows["Wharf"])
        for name in ("Pier", "Slip"):  # Q7 does not exist and "not an id" is no item
            self.assertNotIn("Conversation</a>", rows[name])

    def test_a_missing_or_unreadable_map_means_no_conversation_links(self) -> None:
        home = self.make_home(body=reading([snapshot_task("Dock", title="Sanding the dock")]))
        port = self.start("/qd", workers_home=home, ask_home=self.inbox())
        self.begin(port, "Harbor lamp colours")
        self.assertEqual(self.fetch(port, "/qd/workers.json")[0], 200)
        self.assertNotIn("Conversation</a>", self.panel(self.fetch(port, "/qd/")[1]))
        (home / "data" / "qd-threads").mkdir(parents=True)
        (home / "data" / "qd-threads" / "links.tsv").mkdir()
        self.assertEqual(quarterdeck.read_task_links(home / "data" / "qd-threads" / "links.tsv"), {})
        self.assertNotIn("Conversation</a>", self.panel(self.fetch(port, "/qd/")[1]))

    def test_the_map_file_is_read_and_never_written(self) -> None:
        links = self.root / "map.tsv"
        links.write_text("Dock\tq3\nDock\tQ4\nPier\tQ5\n")
        before = links.read_bytes()
        self.assertEqual(quarterdeck.read_task_links(links), {"Dock": "Q4", "Pier": "Q5"})
        self.assertEqual(links.read_bytes(), before)

    def test_merged_work_leaves_the_tab_after_the_day_ends(self) -> None:
        home = self.make_home(body=reading([snapshot_task("Dock")], [merged("Closed", READING_DAY, title="Painting the hull")]))
        with patch.object(quarterdeck, "local_today", return_value=READING_DAY):
            panel, port = self.served_panel(home)
        self.assertIn("Painting the hull", panel)
        with patch.object(quarterdeck, "local_today", return_value="2026-03-03"):
            panel = self.panel(self.fetch(port, "/qd/")[1])
        self.assertNotIn("Painting the hull", panel)
        self.assertIn(">Building</span>", panel)

    def test_a_saved_reading_from_before_the_tab_is_read_again(self) -> None:
        self.cache_file().parent.mkdir(parents=True, exist_ok=True)
        home = self.make_home([snapshot_task("Dock")])
        old = {"observed_at": SNAPSHOT_TIME, "workers": [], "second_mates": []}
        saved_at = quarterdeck.utc_text(datetime.now(timezone.utc))
        self.cache_file().write_text(json.dumps({"home": str(home.resolve()), "checked_at": saved_at, "snapshot": old}))
        self.assertIsNone(quarterdeck.SavedWorkers(home, self.cache_file()).view()["snapshot"])

    def test_page_text_is_escaped(self) -> None:
        panel, _ = self.served_panel(self.make_home([snapshot_task("Dock", title="<b>Sanding</b>", project="<i>Yard</i>")]))
        self.assertNotIn("<b>", panel)
        self.assertNotIn("<i>", panel)
        self.assertIn("&lt;b&gt;Sanding&lt;/b&gt;", panel)
