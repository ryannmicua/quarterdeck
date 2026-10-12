from __future__ import annotations

import base64
import json
import html
import io
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import quarterdeck
import privacy_guard


def parse_systemd_unit(source: str) -> dict[str, dict[str, list[object]]]:
    unit: dict[str, dict[str, list[object]]] = {}
    section = ""
    for line in source.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            unit.setdefault(section, {})
            continue
        key, separator, value = line.partition("=")
        if not separator or not section:
            raise ValueError(f"invalid systemd directive: {line}")
        if key == "ExecStart":
            value = value.replace("$$", "$").replace("%%", "%")
            parsed: object = shlex.split(value)
        elif key == "Environment":
            words = shlex.split(value.replace("%%", "%"))
            if len(words) != 1:
                raise ValueError(f"invalid systemd environment assignment: {line}")
            parsed = words[0]
        elif key == "WantedBy":
            parsed = value.split()
        else:
            parsed = value
        unit[section].setdefault(key, []).append(parsed)
    return unit


class QuarterdeckTests(unittest.TestCase):
    def make_home(self, root: Path) -> Path:
        home = root / "fictional-firstmate"
        data = home / "data"
        for task_id in ("amber-18", "paper-27", "maple-21", "queued-31", "finished-32", "blocked-33", "internal-34", "captain-36", "orphan-35"):
            (data / task_id).mkdir(parents=True, exist_ok=True)
        (data / "backlog.md").write_text(
            """# Work queue

## Held for review
| ID | Task | State | Held | Hold kind | Hold reason | Report | Closed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| amber-18 | Choose the Amber Kite import format | queued | yes | captain | Pick one format | data/amber-18/report.md | - |

## Review ready
| ID | Task | State | Review ready | PR | Report |
| --- | --- | --- | --- | --- | --- |
| paper-27 | Review the Paper Finch patch | review_ready | yes | """ + "https://github.com/example/quarterdeck-demo/" + "pull/42" + """ | data/paper-27/report.md |

## In flight
| ID | Task | State | Held | Hold kind | Closed |
| --- | --- | --- | --- | --- | --- |
| maple-21 | Chart the Maple Harbor catalog | in_flight | no | - | - |

## Queued
| ID | Task | State | Held | Hold kind | Report | Closed |
| --- | --- | --- | --- | --- | --- | --- |
| queued-31 | Gather the Finch field notes | queued | no | captain | data/queued-31/report.md | - |

## Blocked
| ID | Task | State | Blocked | Waiting on | Blocked reason |
| --- | --- | --- | --- | --- | --- |
| blocked-33 | Confirm the sample vendor schedule | blocked | yes | external | Waiting for the sample vendor response |
| captain-36 | Confirm the sample release window | blocked | yes | captain | Waiting for a captain response |
| internal-34 | Merge the local sample dependency | blocked | yes | maple-21 | Waiting for another backlog item |

## Done
| ID | Task | State | Held | Hold kind | Closed | Report |
| --- | --- | --- | --- | --- | --- | --- |
| finished-32 | File the old Finch notes | done | no | captain | 2026-01-04 | data/finished-32/report.md |
""",
            encoding="utf-8",
        )
        (data / "amber-18" / "report.md").write_text(
            """# Amber Kite import review

## Recommendation

Use the compact format for the first release.
""",
            encoding="utf-8",
        )
        (data / "paper-27" / "report.md").write_text("# Paper Finch patch review\n\nReady for review.\n", encoding="utf-8")
        (data / "queued-31" / "report.md").write_text("# Queued Finch notes\n\nNot ready for review.\n", encoding="utf-8")
        (data / "finished-32" / "report.md").write_text("# Finished Finch report\n\nThis old work is closed.\n", encoding="utf-8")
        (data / "orphan-35" / "report.md").write_text("# Unlinked old report\n\nNo open review task points here.\n", encoding="utf-8")
        secondmate = root / "fictional-secondmate"
        secondmate_data = secondmate / "data"
        (secondmate_data / "kestrel-hold").mkdir(parents=True)
        (secondmate_data / "backlog.md").write_text(
            """# Secondmate queue

| ID | Task | State | Held | Hold kind | Hold reason | Report |
| --- | --- | --- | --- | --- | --- | --- |
| kestrel-work | Chart the Willow Quay field guide | in_flight | no | - | - | - |
| kestrel-hold | Choose the Willow Quay map legend | queued | yes | captain | Select a legend | data/kestrel-hold/report.md |
| kestrel-queued | Collect later map notes | queued | no | - | - | - |
| kestrel-done | Archive the old map draft | done | no | - | - | - |
""",
            encoding="utf-8",
        )
        (secondmate_data / "kestrel-hold" / "report.md").write_text("# Willow Quay legend review\n\nChoose a legend.\n", encoding="utf-8")
        (data / "secondmates.md").write_text(
            f"""# Registered secondmates
- kestrel - Maintains the Willow Quay field guide. (home: {secondmate}; scope: Field guide work; projects: quarterdeck-demo; added 2026-01-04)
- lantern - Maintains remote map notes. (host: sample-remote; root: /srv/sample-firstmate; home: /srv/sample-homes/lantern; scope: Map notes; projects: map-demo; added 2026-01-04)
""",
            encoding="utf-8",
        )
        return home

    def run_cli(self, args: list[str], home: Path, config_home: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(config_home)
        env["XDG_STATE_HOME"] = str(config_home.parent / "state")
        env.pop("FM_HOME", None)
        env.pop("FM_DATA_OVERRIDE", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "quarterdeck.py"), *args],
            check=False,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_render_shows_only_actionable_items_and_matches_header_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "outside-output" / "index.html"
            result = self.run_cli(["render", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            full_page = output.read_text(encoding="utf-8")
            page = full_page.split("Details by home", 1)[1]
            self.assertIn("Choose the Amber Kite import format", page)
            self.assertIn("Use the compact format for the first release.", page)
            self.assertIn("https://github.com/example/quarterdeck-demo/" + "pull/42", page)
            self.assertIn("Chart the Maple Harbor catalog", page)
            self.assertIn("kestrel — Maintains the Willow Quay field guide.", page)
            self.assertIn("Chart the Willow Quay field guide", page)
            self.assertIn("Secondmate · registered by fictional-firstmate", page)
            self.assertIn("remote host sample-remote", page)
            self.assertIn("Read report", page)
            self.assertNotIn("Gather the Finch field notes", page)
            self.assertIn("Reports needing review", page)
            self.assertIn("Finished Finch report", page)
            self.assertIn("Unlinked old report", page)
            orphan_path = home / "data" / "orphan-35" / "report.md"
            orphan_page = quarterdeck.source_page_filename("report", home, orphan_path)
            self.assertIn('<code>fictional-firstmate/orphan-35</code>', page)
            self.assertIn(f'href="{orphan_page}">Read report <code>fictional-firstmate/orphan-35</code>', page)
            self.assertNotIn("Merge the local sample dependency", page)
            self.assertNotIn("file://", page)
            self.assertIn("Read backlog item", page)
            self.assertRegex(page, r'<b>2</b><span>Held for captain</span>')
            self.assertRegex(page, r'<b>1</b><span>Review-ready PRs</span>')
            self.assertRegex(page, r'<b>2</b><span>In-flight work</span>')
            self.assertRegex(page, r'<b>2</b><span>Blocked for captain/external</span>')
            for label, badge in (
                ("Held for captain", "Held for captain"),
                ("Review-ready PRs", "Review-ready pull request"),
                ("In-flight work", "In-flight work"),
                ("Blocked for captain/external", "Blocked for captain or external party"),
            ):
                metric = re.search(r'<div class="metric"><b>(\d+)</b><span>' + re.escape(label) + r"</span></div>", page)
                self.assertIsNotNone(metric)
                self.assertEqual(int(metric.group(1)), page.count(f'<p class="eyebrow">{badge}</p>'))
            self.assertIn('name="quarterdeck-' + "generated" + '"', full_page)
            self.assertNotIn("https://fonts.", page)
            report_pages = list(output.parent.glob("report-*.html"))
            self.assertEqual(len(report_pages), 6)
            self.assertTrue(all(path.is_file() for path in report_pages))
            backlog_pages = list(output.parent.glob("backlog-*.html"))
            self.assertEqual(len(backlog_pages), 2)
            self.assertTrue(any("<h1>Work queue</h1>" in path.read_text(encoding="utf-8") for path in backlog_pages))

    def test_lavish_render_creates_annotatable_page_and_opens_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "output" / "index.html"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            stub = bin_dir / "lavish-axi"
            stub.write_text("#!/bin/sh\nprintf '%s\\n' 'Session: https://review.example.invalid/session/fictional'\n", encoding="utf-8")
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
            env["XDG_STATE_HOME"] = str(root / "state")
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--lavish", "--home", str(home), "--output", str(output)],
                check=False, text=True, capture_output=True, env=env,
            )
            lavish_page = output.with_name("index.lavish.html")
            page = lavish_page.read_text(encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.parent.exists())
            self.assertFalse(output.exists())
            self.assertIn("https://review.example.invalid/session/fictional", result.stdout)
            self.assertRegex(page, r'<article class="card" id="review-item-[0-9a-f]{12}">')
            self.assertIn("queuePrompt", page)
            self.assertIn("Request a Lavish page", page)
            self.assertRegex(page, r'<article class="card" id="review-report-[0-9a-f]{12}">')
            self.assertIn("Reports needing review", page)
            homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec(home.name, home), discover_secondmates=True)]
            expected = quarterdeck.build_html(
                homes, "Quarterdeck", lavish=True, bearings=quarterdeck.build_bearings(homes),
            )
            stamp = re.compile(r"[Gg]enerated(?: at)? \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC")
            self.assertEqual(stamp.sub("", page), stamp.sub("", expected))

    def test_all_mode_restores_queued_finished_and_unlinked_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "outside-output" / "index.html"
            result = self.run_cli(["render", "--all", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            page = output.read_text(encoding="utf-8").split("Details by home", 1)[1]
            self.assertIn("Gather the Finch field notes", page)
            self.assertIn("File the old Finch notes", page)
            self.assertIn("Unlinked old report", page)
            self.assertIn("Other backlog items", page)
            self.assertIn("All scout reports", page)
            self.assertRegex(page, r'<b>5</b><span>Other backlog items</span>')
            self.assertRegex(page, r'<b>6</b><span>Scout reports</span>')
            self.assertEqual(page.count('<p class="eyebrow">Other backlog item</p>'), 5)
            self.assertEqual(page.count("<h3>Finished Finch report</h3>"), 1)
            self.assertEqual(page.count("<h3>Unlinked old report</h3>"), 1)

    def test_all_lavish_request_control_is_limited_to_unreviewed_report_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            config_home = root / "config"
            report_id = "fictional-firstmate/amber-18"

            marked = self.run_cli(
                ["reports", "mark-reviewed", report_id, "--home", str(home)],
                root, config_home,
            )
            self.assertEqual(marked.returncode, 0, marked.stderr)

            empty_bin = root / "empty-bin"
            empty_bin.mkdir()
            output = root / "dashboard.html"
            env = os.environ.copy()
            env["HOME"] = str(root)
            env["XDG_CONFIG_HOME"] = str(config_home)
            env["XDG_STATE_HOME"] = str(root / "state")
            env["PATH"] = str(empty_bin)
            env.pop("FM_HOME", None)
            env.pop("FM_DATA_OVERRIDE", None)
            rendered = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--all", "--lavish",
                 "--home", str(home), "--output", str(output)],
                check=False, text=True, capture_output=True, env=env,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)

            page = output.with_name("dashboard.lavish.html").read_text(encoding="utf-8")
            report_cards = re.findall(r'<article class="card" id="review-report-[^"]+">.*?</article>', page, re.DOTALL)
            reviewed_card = next(card for card in report_cards if "<h3>Amber Kite import review</h3>" in card)
            unreviewed_card = next(card for card in report_cards if "<h3>Unlinked old report</h3>" in card)
            self.assertIn("Scout report · Reviewed", reviewed_card)
            self.assertNotIn("data-lavish-request", reviewed_card)
            self.assertIn("Scout report · Needs review", unreviewed_card)
            self.assertIn("data-lavish-request", unreviewed_card)

            payloads = [
                json.loads(html.unescape(value))
                for value in re.findall(r'data-lavish-request="([^"]+)"', page)
            ]
            self.assertTrue(any(payload.get("report_id") == report_id for payload in payloads))

    def test_lavish_request_control_has_structured_payload_and_is_hidden_on_plain_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            plain = root / "plain.html"
            plain_result = self.run_cli(["render", "--home", str(home), "--output", str(plain)], root, root / "config")
            self.assertEqual(plain_result.returncode, 0, plain_result.stderr)
            self.assertNotIn("data-lavish-request", plain.read_text(encoding="utf-8"))
            self.assertNotIn("Request a Lavish page", plain.read_text(encoding="utf-8"))

            lavish_base = root / "lavish.html"
            bin_dir = root / "empty-bin"
            bin_dir.mkdir()
            stub = bin_dir / "lavish-axi"
            stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir)
            env["XDG_STATE_HOME"] = str(root / "state")
            lavish_result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--lavish", "--home", str(home), "--output", str(lavish_base)],
                text=True, capture_output=True, env=env,
            )
            self.assertEqual(lavish_result.returncode, 0, lavish_result.stderr)
            page = lavish_base.with_name("lavish.lavish.html").read_text(encoding="utf-8")
            payloads = [
                json.loads(html.unescape(value))
                for value in re.findall(r'data-lavish-request="([^"]+)"', page)
            ]
            self.assertEqual(len(payloads), 11)
            self.assertEqual(payloads[0], {
                "item_id": "fictional-firstmate/amber-18",
                "title": "Amber Kite import review",
                "kind": "report",
                "home_label": "fictional-firstmate",
                "source_path": "data/amber-18/report.md",
                "report_id": "fictional-firstmate/amber-18",
            })
            self.assertIn({
                "item_id": "fictional-firstmate/amber-18",
                "report_id": "fictional-firstmate/amber-18",
                "title": "Amber Kite import review",
                "kind": "report",
                "home_label": "fictional-firstmate",
                "source_path": "data/amber-18/report.md",
            }, payloads)
            self.assertIn("backlog item", {payload["kind"] for payload in payloads})
            self.assertIn("fictional-firstmate", {payload["home_label"] for payload in payloads})
            self.assertIn("kestrel — Maintains the Willow Quay field guide.", {payload["home_label"] for payload in payloads})

    def test_report_commands_read_and_track_review_state_by_content_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            config_home = root / "config"
            report_id = "fictional-firstmate/amber-18"

            listed = self.run_cli(["reports", "list", "--home", str(home)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(f"{report_id}\tneeds review\tAmber Kite import review", listed.stdout)

            read = self.run_cli(
                ["reports", "read", report_id, "--home", str(home)],
                root, config_home,
            )
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertIn("# Amber Kite import review", read.stdout)
            page_path = Path(read.stdout.split("Readable HTML: ", 1)[1].strip())
            self.assertEqual(
                page_path,
                root / "state" / "quarterdeck" / quarterdeck.source_page_filename(
                    "report", home, home / "data" / "amber-18" / "report.md",
                ),
            )
            self.assertTrue(page_path.is_file())
            self.assertIn("<h1>Amber Kite import review</h1>", page_path.read_text(encoding="utf-8"))

            marked = self.run_cli(["reports", "mark-reviewed", report_id, "--home", str(home)], root, config_home)
            self.assertEqual(marked.returncode, 0, marked.stderr)
            listed = self.run_cli(["reports", "list", "--home", str(home)], root, config_home)
            self.assertIn(f"{report_id}\treviewed\tAmber Kite import review", listed.stdout)

            state_path = root / "state" / "quarterdeck" / "review-state" / "state.db"
            report_path = home / "data" / "amber-18" / "report.md"
            with sqlite3.connect(state_path) as db:
                rows = db.execute("SELECT report_id, state, fingerprint FROM report_state").fetchall()
            self.assertEqual(
                rows, [(report_id, "reviewed", quarterdeck.hashlib.sha256(report_path.read_bytes()).hexdigest())]
            )
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            self.assertFalse(state_path.is_relative_to(home))
            self.assertFalse(state_path.is_relative_to(ROOT))

            output = root / "dashboard.html"
            rendered = self.run_cli(["render", "--home", str(home), "--output", str(output)], root, config_home)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            dashboard = output.read_text(encoding="utf-8")
            self.assertIn("Reports needing review", dashboard)
            self.assertNotIn("<h3>Amber Kite import review</h3>", dashboard)

            report_path.write_text(report_path.read_text(encoding="utf-8") + "\nUpdated sample.\n", encoding="utf-8")
            listed = self.run_cli(["reports", "list", "--home", str(home)], root, config_home)
            self.assertIn(f"{report_id}\tneeds review\tAmber Kite import review", listed.stdout)

            unmarked = self.run_cli(["reports", "unmark-reviewed", report_id, "--home", str(home)], root, config_home)
            self.assertEqual(unmarked.returncode, 0, unmarked.stderr)
            self.assertNotIn(report_id, quarterdeck.load_review_marks(state_path.with_name("marks.json")))

    def test_report_id_collisions_are_listed_and_refused_for_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_home = self.make_home(root / "first")
            second_home = self.make_home(root / "second")
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "Sample Bay", "path": str(first_home)},
            ]}), encoding="utf-8")
            report_id = "sample-bay/amber-18"

            marked = self.run_cli(["reports", "mark-reviewed", report_id, "--config", str(config)], root, config_home)
            self.assertEqual(marked.returncode, 0, marked.stderr)

            config.write_text(json.dumps({"homes": [
                {"label": "Sample Bay", "path": str(first_home)},
                {"label": "sample-bay", "path": str(second_home)},
            ]}), encoding="utf-8")

            listed = self.run_cli(["reports", "list", "--config", str(config)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(listed.stdout.count(f"{report_id}\tID collision"), 2)

            output = root / "dashboard.html"
            rendered = self.run_cli(["render", "--config", str(config), "--output", str(output)], root, config_home)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            dashboard = output.read_text(encoding="utf-8")
            report_card = f'<p class="eyebrow">Scout report · Needs review · <code>{report_id}</code></p>'
            self.assertEqual(dashboard.count(report_card), 2)

            read = self.run_cli(["reports", "read", report_id, "--config", str(config)], root, config_home)
            self.assertEqual(read.returncode, 2)
            self.assertIn("report ID is ambiguous", read.stderr)

            marked = self.run_cli(["reports", "mark-reviewed", report_id, "--config", str(config)], root, config_home)
            self.assertEqual(marked.returncode, 2)
            self.assertIn("report ID is ambiguous", marked.stderr)

    def test_report_list_surfaces_failed_homes_and_resolution_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root / "available")
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "Available Sample", "path": str(home)},
                {"label": "Missing Sample", "path": str(root / "missing-firstmate")},
            ]}), encoding="utf-8")
            report_id = "available-sample/amber-18"

            listed = self.run_cli(["reports", "list", "--config", str(config)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(f"{report_id}\tneeds review", listed.stdout)
            self.assertIn("Discovery error (Missing Sample):", listed.stdout)
            self.assertIn("Firstmate home is not a directory", listed.stdout)

            for action in ("read", "mark-reviewed", "unmark-reviewed"):
                with self.subTest(action=action):
                    result = self.run_cli(["reports", action, report_id, "--config", str(config)], root, config_home)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("cannot resolve report ID", result.stderr)
                    self.assertIn("Missing Sample", result.stderr)

    def test_unreadable_report_is_listed_and_blocks_ambiguous_id_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_home = self.make_home(root / "first")
            second_home = self.make_home(root / "second")
            unreadable_report = second_home / "data" / "amber-18" / "report.md"
            unreadable_report.unlink()
            unreadable_report.mkdir()
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "Sample Bay", "path": str(first_home)},
                {"label": "sample-bay", "path": str(second_home)},
            ]}), encoding="utf-8")
            report_id = "sample-bay/amber-18"

            listed = self.run_cli(["reports", "list", "--config", str(config)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(f"{report_id}\tneeds review", listed.stdout)
            self.assertIn("Discovery error (sample-bay):", listed.stdout)
            self.assertIn("Could not read report", listed.stdout)

            for action in ("read", "mark-reviewed", "unmark-reviewed"):
                with self.subTest(action=action):
                    result = self.run_cli(["reports", action, report_id, "--config", str(config)], root, config_home)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("cannot resolve report ID", result.stderr)
                    self.assertIn("sample-bay", result.stderr)

    def test_concurrent_review_mark_updates_preserve_every_mark(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marks_path = Path(temp) / "review-state" / "marks.json"
            errors: list[BaseException] = []

            def apply_mark(index: int) -> None:
                try:
                    quarterdeck.update_review_mark(marks_path, f"sample/report-{index}", f"{index:064x}")
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=apply_mark, args=(index,)) for index in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            self.assertEqual(errors, [])
            self.assertEqual(
                quarterdeck.load_review_marks(marks_path),
                {f"sample/report-{index}": f"{index:064x}" for index in range(12)},
            )

    def test_reports_read_retains_html_path_when_browser_open_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            environment = {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
            }

            with patch.dict(os.environ, environment, clear=True):
                with patch.object(quarterdeck.webbrowser, "open", return_value=False):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = quarterdeck.main([
                            "reports", "read", "fictional-firstmate/amber-18",
                            "--home", str(home), "--open",
                        ])

            self.assertEqual(result, 2)
            self.assertIn("Readable HTML:", stdout.getvalue())
            output_path = Path(stdout.getvalue().split("Readable HTML: ", 1)[1].strip())
            self.assertTrue(output_path.is_file())
            self.assertIn("could not open report page in a browser", stderr.getvalue())

    def test_reports_read_reports_browser_exception_and_retains_html_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            environment = {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
            }

            with patch.dict(os.environ, environment, clear=True):
                with patch.object(quarterdeck.webbrowser, "open", side_effect=quarterdeck.webbrowser.Error("no browser registered")):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = quarterdeck.main([
                            "reports", "read", "fictional-firstmate/amber-18",
                            "--home", str(home), "--open",
                        ])

            self.assertEqual(result, 2)
            self.assertIn("Readable HTML:", stdout.getvalue())
            output_path = Path(stdout.getvalue().split("Readable HTML: ", 1)[1].strip())
            self.assertTrue(output_path.is_file())
            self.assertIn("no browser registered", stderr.getvalue())

    def test_review_state_inside_configured_data_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            data_dir = root / "external-data"
            (data_dir / "amber-18").mkdir(parents=True)
            (data_dir / "backlog.md").write_text("# Work queue\n", encoding="utf-8")
            (data_dir / "amber-18" / "report.md").write_text("# External report\n", encoding="utf-8")
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "External Sample", "path": str(home), "data_dir": str(data_dir)},
            ]}), encoding="utf-8")
            env = os.environ.copy()
            env["HOME"] = str(root)
            env["XDG_CONFIG_HOME"] = str(config_home)
            env["XDG_STATE_HOME"] = str(data_dir)
            env.pop("FM_HOME", None)
            env.pop("FM_DATA_OVERRIDE", None)

            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "reports", "mark-reviewed", "external-sample/amber-18", "--config", str(config)],
                check=False, text=True, capture_output=True, env=env,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("review state must be outside", result.stderr)
            self.assertFalse((data_dir / "quarterdeck" / "review-state").exists())

    def test_long_distinct_home_labels_keep_distinct_report_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_home = self.make_home(root / "first")
            second_home = self.make_home(root / "second")
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "northstar-field-research-center-east", "path": str(first_home)},
                {"label": "northstar-field-research-center-west", "path": str(second_home)},
            ]}), encoding="utf-8")

            listed = self.run_cli(["reports", "list", "--config", str(config)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("northstar-field-research-center-east/amber-18\tneeds review", listed.stdout)
            self.assertIn("northstar-field-research-center-west/amber-18\tneeds review", listed.stdout)

    def test_review_state_inside_a_firstmate_home_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            env = os.environ.copy()
            env["HOME"] = str(root)
            env["XDG_CONFIG_HOME"] = str(root / "config")
            env["XDG_STATE_HOME"] = str(home)
            env.pop("FM_HOME", None)
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "reports", "mark-reviewed", "fictional-firstmate/amber-18", "--home", str(home)],
                check=False, text=True, capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("review state must be outside", result.stderr)
            self.assertFalse((home / "quarterdeck" / "review-state").exists())

    def test_rendered_report_markdown_escapes_html_and_formats_common_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            report = home / "data" / "amber-18" / "report.md"
            report.write_text(
                """# Safe sample report

<script>runFixture()</script>

**Bold text** and *emphasized text* with [a safe link](https://example.invalid/guide).

- First item
- Second item

| Name | Value |
| --- | --- |
| Example | visible |

\x60\x60\x60html
<img src=x onerror=runFixture()>
\x60\x60\x60
""",
                encoding="utf-8",
            )
            output = root / "outside-output" / "index.html"
            result = self.run_cli(["render", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered_path = output.parent / quarterdeck.source_page_filename("report", home, report)
            rendered = rendered_path.read_text(encoding="utf-8")
            self.assertIn("<h1>Safe sample report</h1>", rendered)
            self.assertIn("&lt;script&gt;runFixture()&lt;/script&gt;", rendered)
            self.assertNotIn("<script>runFixture()</script>", rendered)
            self.assertIn("<strong>Bold text</strong>", rendered)
            self.assertIn("<em>emphasized text</em>", rendered)
            self.assertIn("<ul><li>First item</li><li>Second item</li></ul>", rendered)
            self.assertIn("<table>", rendered)
            self.assertIn("<a href=\"https://example.invalid/guide\">a safe link</a>", rendered)
            self.assertIn("<pre><code>&lt;img src=x onerror=runFixture()&gt;</code></pre>", rendered)

    def test_lavish_render_without_cli_prints_open_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            empty_path = root / "empty-bin"
            empty_path.mkdir()
            env = os.environ.copy()
            env["PATH"] = str(empty_path)
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--lavish", "--home", str(home), "--output", str(root / "page.html")],
                check=False, text=True, capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Lavish page ready; open it later with: lavish-axi", result.stdout)
            self.assertTrue((root / "page.lavish.html").is_file())

    def test_configured_homes_render_together_and_isolate_a_missing_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            config = root / "quarterdeck.json"
            config.write_text(
                json.dumps({
                    "page_title": "Fleet Example",
                    "homes": [
                        {"label": "Main Harbor", "path": home.name},
                        {"label": "Unreadable Lantern", "path": "missing-home"},
                    ],
                }),
                encoding="utf-8",
            )
            output = root / "outside-output" / "index.html"
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--config", str(config), "--output", str(output)],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            page = output.read_text(encoding="utf-8")
            self.assertIn("Main Harbor", page)
            self.assertIn("Unreadable Lantern", page)
            self.assertIn("Home could not be read", page)
            self.assertIn("Chart the Maple Harbor catalog", page)

    def test_home_argument_overrides_configured_home_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            config = root / "quarterdeck.json"
            config.write_text(
                json.dumps({"homes": [{"label": "Configured Other", "path": "missing-home"}]}),
                encoding="utf-8",
            )
            output = root / "outside-output" / "index.html"
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--home", str(home), "--config", str(config), "--output", str(output)],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            page = output.read_text(encoding="utf-8")
            self.assertIn("Chart the Maple Harbor catalog", page)
            self.assertNotIn("Configured Other", page)

    def test_render_uses_fm_home_for_backward_compatible_single_home_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "outside-output" / "index.html"
            with patch.dict(os.environ, {"FM_HOME": str(home), "XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                result = quarterdeck.main(["render", "--output", str(output)])
            self.assertEqual(result, 0)
            self.assertIn("Chart the Maple Harbor catalog", output.read_text(encoding="utf-8"))

    def test_render_requires_home_or_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"HOME": temp, "XDG_CONFIG_HOME": str(Path(temp) / "config")}, clear=True
        ):
            result = quarterdeck.main(["render"])
        self.assertEqual(result, 2)

    def test_unreadable_single_home_uses_secondmate_board_only_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            (home / "data" / "backlog.md").unlink()
            secondmate = root / "fictional-secondmate"
            (secondmate / "data" / "captain-board.json").write_text(json.dumps({
                "version": 1,
                "updated_at": "2026-10-08T10:00:00Z",
                "items": [{
                    "id": "secondmate-approval",
                    "group": "approve",
                    "title": "Secondmate approval",
                    "ask": "Approve the field guide handover.",
                    "task": "kestrel-hold",
                }],
            }), encoding="utf-8")
            output = root / "outside-output" / "index.html"
            result = self.run_cli(
                ["render", "--home", str(home), "--no-snapshot", "--output", str(output)],
                root, root / "config",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Secondmate approval", output.read_text(encoding="utf-8"))

            (secondmate / "data" / "captain-board.json").unlink()
            no_board_output = root / "outside-output" / "without-board.html"
            failed = self.run_cli(
                ["render", "--home", str(home), "--no-snapshot", "--output", str(no_board_output)],
                root, root / "config",
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("backlog not found", failed.stderr)
            self.assertFalse(no_board_output.exists())

    def test_render_refuses_output_inside_a_selected_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = home / "index.html"
            result = self.run_cli(["render", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the Quarterdeck checkout", result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(list(home.rglob("report-*.html")))

    def test_add_list_remove_default_config_and_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli_home = root / "user"
            config_home = cli_home / ".config"
            firstmate_home = self.make_home(root)

            added = self.run_cli(["add", str(firstmate_home)], cli_home, config_home)
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertIn('Using default label "fictional-firstmate".', added.stdout)
            config_path = config_home / "quarterdeck.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["homes"], [{"label": "fictional-firstmate", "path": str(firstmate_home.resolve())}])
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

            listed = self.run_cli(["list"], cli_home, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(f"fictional-firstmate\t{firstmate_home.resolve()}", listed.stdout)

            output = root / "outside-output" / "index.html"
            rendered = self.run_cli(["render", "--output", str(output)], cli_home, config_home)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn("Chart the Maple Harbor catalog", output.read_text(encoding="utf-8"))

            removed = self.run_cli(["remove", str(firstmate_home)], cli_home, config_home)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertIn("Firstmate data was not changed", removed.stdout)
            self.assertTrue((firstmate_home / "data" / "backlog.md").is_file())
            self.assertEqual(self.run_cli(["list"], cli_home, config_home).stdout.strip(), "No homes registered.")
            self.assertNotIn("homes", json.loads(config_path.read_text(encoding="utf-8")))

    def test_add_preserves_config_keys_and_refuses_invalid_or_duplicate_homes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli_home = root / "user"
            config_home = cli_home / ".config"
            config_home.mkdir(parents=True)
            config_path = config_home / "quarterdeck.json"
            config_path.write_text(json.dumps({"page_title": "Private fleet", "output_dir": "~/review"}), encoding="utf-8")
            firstmate_home = self.make_home(root)
            other_home = root / "fictional-other"
            (other_home / "data").mkdir(parents=True)
            (other_home / "data" / "backlog.md").write_text("# Empty queue\n", encoding="utf-8")

            added = self.run_cli(["add", str(firstmate_home), "--label", "Maple Harbor"], cli_home, config_home)
            self.assertEqual(added.returncode, 0, added.stderr)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["page_title"], "Private fleet")
            self.assertEqual(config["output_dir"], "~/review")
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

            duplicate_path = self.run_cli(["add", str(firstmate_home / ".." / firstmate_home.name)], cli_home, config_home)
            self.assertEqual(duplicate_path.returncode, 2)
            self.assertIn("already registered", duplicate_path.stderr)

            duplicate_label = self.run_cli(["add", str(other_home), "--label", "maple harbor"], cli_home, config_home)
            self.assertEqual(duplicate_label.returncode, 2)
            self.assertIn("label is already in use", duplicate_label.stderr)

            invalid_home = root / "not-a-firstmate"
            invalid_home.mkdir()
            invalid = self.run_cli(["add", str(invalid_home)], cli_home, config_home)
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("expected data/backlog.md", invalid.stderr)
            self.assertEqual(len(json.loads(config_path.read_text(encoding="utf-8"))["homes"]), 1)

            second_added = self.run_cli(["add", str(other_home), "--label", "Willow Quay"], cli_home, config_home)
            self.assertEqual(second_added.returncode, 0, second_added.stderr)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["page_title"], "Private fleet")
            self.assertEqual([entry["label"] for entry in config["homes"]], ["Maple Harbor", "Willow Quay"])

            removed = self.run_cli(["remove", "Maple Harbor"], cli_home, config_home)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual(len(json.loads(config_path.read_text(encoding="utf-8"))["homes"]), 1)
            missing = self.run_cli(["remove", "Maple Harbor"], cli_home, config_home)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("no registered home matches", missing.stderr)
            self.assertTrue((other_home / "data" / "backlog.md").is_file())

    def test_manage_commands_refuse_config_inside_checkout(self) -> None:
        home = Path(tempfile.mkdtemp())
        config = ROOT / "quarterdeck.local.json"
        try:
            result = self.run_cli(["add", str(home), "--config", str(config)], home, home / ".config")
            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the Quarterdeck checkout", result.stderr)
            self.assertFalse(config.exists())
        finally:
            home.rmdir()

    def test_help_exposes_all_commands_docs_and_machine_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli_home = root / "user"
            config_home = cli_home / ".config"
            root_help = self.run_cli(["--help"], cli_home, config_home)
            self.assertEqual(root_help.returncode, 0, root_help.stderr)
            self.assertIn("update", root_help.stdout)

            general = self.run_cli(["help"], cli_home, config_home)
            self.assertEqual(general.returncode, 0, general.stderr)
            self.assertIn("Documentation tree:", general.stdout)
            self.assertIn("quarterdeck help --json", general.stdout)

            machine = self.run_cli(["help", "--json"], cli_home, config_home)
            self.assertEqual(machine.returncode, 0, machine.stderr)
            index = json.loads(machine.stdout)
            commands = {entry["name"]: entry for entry in index["commands"]}
            self.assertTrue({"install", "update", "add", "list", "remove", "render", "reports", "help"}.issubset(commands))
            self.assertIn("how-to/install.md", commands["install"]["docs"])
            self.assertIn("how-to/update.md", commands["update"]["docs"])
            self.assertIn("--lavish", commands["render"]["usage"])
            self.assertIn("--all", commands["render"]["usage"])
            self.assertIn("explanation/attention-model.md", commands["render"]["docs"])
            self.assertIn("how-to/request-lavish-page.md", commands["render"]["docs"])
            self.assertIn("Lavish", commands["render"]["summary"])
            self.assertIn("how-to/review-reports.md", commands["reports"]["docs"])

            for command in ("install", "update", "add", "list", "remove", "render", "reports"):
                with self.subTest(command=command):
                    result = self.run_cli([command, "--help"], cli_home, config_home)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if command == "update":
                        self.assertIn("Run the user-local shell installer to update", result.stdout)
                    if command == "render":
                        self.assertIn("--lavish", result.stdout)
                        self.assertIn("--all", result.stdout)

            for subcommand in ("list", "read", "mark-reviewed", "unmark-reviewed"):
                result = self.run_cli(["reports", subcommand, "--help"], cli_home, config_home)
                self.assertEqual(result.returncode, 0, result.stderr)
                if subcommand == "read":
                    self.assertIn("--open", result.stdout)
                    self.assertNotIn("--output-dir", result.stdout)

    def test_installer_clones_updates_reuses_and_uninstalls_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / "quarterdeck-fixture.git"
            source = root / "source"
            user_home = root / "user"
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True, text=True)
            source.mkdir()
            subprocess.run(["git", "init", "--initial-branch=main"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "Quarterdeck Fixture"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=source, check=True)
            (source / "install.sh").write_bytes((ROOT / "install.sh").read_bytes())
            (source / "quarterdeck.py").write_text("#!/usr/bin/env python3\nprint('fixture')\n", encoding="utf-8")
            (source / "README.md").write_text("fixture version one\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v1"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=source, check=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=source, check=True, capture_output=True, text=True)

            env = os.environ.copy()
            env["HOME"] = str(user_home)
            env["QUARTERDECK_REPO_URL"] = str(remote)
            env["PATH"] = os.environ.get("PATH", "")
            config_path = user_home / ".config" / "quarterdeck.json"
            installer = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, text=True, capture_output=True)
            self.assertEqual(installer.returncode, 0, installer.stderr)
            self.assertTrue((user_home / ".local/share/quarterdeck/README.md").is_file())
            self.assertEqual(config_path.read_text(encoding="utf-8"), "{}\n")
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((user_home / ".config").stat().st_mode), 0o700)
            self.assertEqual(os.readlink(user_home / ".local/bin/quarterdeck"), str(user_home / ".local/share/quarterdeck/quarterdeck.py"))
            self.assertIn("not on PATH", installer.stdout)

            repeated = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, text=True, capture_output=True)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(config_path.read_text(encoding="utf-8"), "{}\n")

            (user_home / ".config").mkdir(parents=True, exist_ok=True)
            config_path.write_text('{"page_title":"Private setup"}\n', encoding="utf-8")
            (source / "README.md").write_text("fixture version two\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v2"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "push"], cwd=source, check=True, capture_output=True, text=True)
            updated = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "install"], env=env, text=True, capture_output=True
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            installed = user_home / ".local/share/quarterdeck"
            self.assertEqual((installed / "README.md").read_text(encoding="utf-8"), "fixture version two\n")
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"page_title":"Private setup"}\n')

            (source / "README.md").write_text("fixture version three\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v3"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "push"], cwd=source, check=True, capture_output=True, text=True)
            updated_again = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "update"], env=env, text=True, capture_output=True
            )
            self.assertEqual(updated_again.returncode, 0, updated_again.stderr)
            self.assertEqual((installed / "README.md").read_text(encoding="utf-8"), "fixture version three\n")
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"page_title":"Private setup"}\n')

            uninstall = subprocess.run(["sh", str(installed / "install.sh"), "--uninstall"], env=env, text=True, capture_output=True)
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertFalse((user_home / ".local/bin/quarterdeck").exists())
            self.assertFalse(installed.exists())
            self.assertTrue(config_path.is_file())
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"page_title":"Private setup"}\n')

            reused_install = user_home / ".local/share/quarterdeck"
            subprocess.run(["git", "clone", str(remote), str(reused_install)], check=True, capture_output=True, text=True)
            reused = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, text=True, capture_output=True)
            self.assertEqual(reused.returncode, 0, reused.stderr)
            (reused_install / "README.md").write_text("local edit\n", encoding="utf-8")
            (source / "README.md").write_text("fixture version four\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v4"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "push"], cwd=source, check=True, capture_output=True, text=True)
            refused = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "update"], env=env, text=True, capture_output=True
            )
            self.assertEqual(refused.returncode, 1)
            self.assertIn("has local changes", refused.stderr)
            self.assertEqual((reused_install / "README.md").read_text(encoding="utf-8"), "local edit\n")

            reused_uninstall = subprocess.run(["sh", str(reused_install / "install.sh"), "--uninstall"], env=env, text=True, capture_output=True)
            self.assertEqual(reused_uninstall.returncode, 0, reused_uninstall.stderr)
            self.assertFalse((user_home / ".local/bin/quarterdeck").exists())
            self.assertTrue(reused_install.is_dir())
            self.assertTrue(config_path.is_file())

    def test_recommendation_requires_an_explicit_report_label(self) -> None:
        self.assertIsNone(quarterdeck.report_recommendation("# Notes\n\nA strong recommendation is hidden here."))
        self.assertEqual(
            quarterdeck.report_recommendation("## Recommendation\n\nChoose the north path."),
            "Choose the north path.",
        )


PIER_PR = "https://github.com/example/pier-demo/" + "pull/7"
BULLET_BACKLOG = """# Backlog

## In flight

- [ ] harbor-chart - Harbor chart: draw the tide tables (repo: harbor-demo) (kind: ship) (since 2026-09-30)
  Drawing tides for the invented harbor.

## Queued

- [ ] kelp-survey - Kelp survey: map the invented reef data/kelp-survey/report.md blocked-by: harbor-chart (repo: reef-demo) (kind: ship) (since 2026-09-20) (held: yes) (hold: Waiting on the invented diver roster. Options: (a) survey now, (b) wait for spring. Recommendation: wait for spring (cheaper).) (hold-kind: captain)

  Filed 2026-09-20T08:00:00Z. The reef survey needs a choice (see data/kelp-survey/report.md).

  Second paragraph with the long body text that must stay on the page.
- [ ] later-work - Later work: sort the invented buoys (repo: buoy-demo) (kind: ship) (since 2026-09-25)

## Done

- [x] pier-fix - Pier fix: patch the invented pier (repo: pier-demo) (kind: ship) (merged 2026-10-01 """ + PIER_PR + """)
- [x] old-dock - Old dock: retire the invented dock (repo: dock-demo) (done 2026-08-01)
"""

SNAPSHOT_JSON = {
    "schema": "fm-bearings.v1",
    "home": "invented",
    "generated": "2026-10-04T00:00:00Z",
    "in_flight": [{"id": "harbor-chart", "kind": "ship", "state": "working", "repo": "harbor-demo",
                   "name": "Harbor chart", "doing": "drawing tides"}],
    "secondmates": [{"id": "kestrel", "state": "no_active_work", "doing": "mapping", "reason": "-"}],
    "decisions_open": [{"id": "kelp-survey", "key": "kelp-survey", "verb": "captain-hold",
                        "summary": "Kelp survey: map the invented reef (truncated sum", "owner": "(main)"}],
    "landed": [{"id": "pier-fix", "what": "Pier fix: patch the invented pier", "artifact":
                PIER_PR, "owner": "(main)"}],
    "gates": [{"id": "later-work", "title": "Later work", "blocked_by": "harbor-chart",
               "reason": "queued behind the chart", "owner": "(main)", "filed": "2026-09-25"}],
    "reports": [], "recorded_prs": [],
}


BOARD_BACKLOG = """# Backlog

## Queued

- [ ] kelp-hold - Kelp hold: pick a survey date (repo: reef-demo) (held: yes) (hold: Raw note: choose spring or autumn.) (hold-kind: captain)
- [ ] reef-hold - Reef hold: choose the buoy colour (repo: reef-demo) (held: yes) (hold: Raw note: red or green.) (hold-kind: captain)
- [ ] pier-pr - Pier patch (repo: pier-demo) (state: review_ready) (review_ready: yes) (pr """ + PIER_PR + """)
- [ ] waiting-work - Waiting work (repo: pier-demo) (held: yes) (hold: Raw note: waits on a vendor.) (hold-kind: external)

## Done

- [x] dock-fix - Dock fix: patch the invented dock (repo: dock-demo) (merged 2026-10-01)
"""


def board_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {"id": "pier-merge", "group": "merge", "title": "Pier merge",
                               "ask": "Merge the pier patch when you are ready.", "task": "pier-pr",
                               "links": [{"label": "Pier PR", "url": PIER_PR}]}
    item.update(overrides)
    return item


class CaptainBoardTests(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def make_home(self, root: Path, items: object = None, raw: str | None = None, **board: object) -> Path:
        home = root / "invented-home"
        (home / "data").mkdir(parents=True)
        (home / "data" / "backlog.md").write_text(BOARD_BACKLOG, encoding="utf-8")
        if raw is not None:
            (home / "data" / "captain-board.json").write_text(raw, encoding="utf-8")
        elif items is not None:
            payload = {"version": 1, "updated_at": "2099-01-01T00:00:00Z", "items": items}
            payload.update(board)
            (home / "data" / "captain-board.json").write_text(json.dumps(payload), encoding="utf-8")
        return home

    def view(self, home: Path):
        homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("invented", home), discover_secondmates=True)]
        return homes, quarterdeck.board_view(homes)

    def titles(self, view, group: str) -> list[str]:
        return [entry.item.title for entry in view.groups[group]]

    def test_absent_board_leaves_page_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            homes, view = self.view(self.make_home(Path(temp)))
            self.assertIsNone(view)
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertNotIn('id="board"', page)
            self.assertNotIn("Not yet sorted", page)
            self.assertNotIn("<details class=\"dashboard-section reviews\"", page)
            self.assertIn('<div class="dashboard-section reviews" id="reviews">', page)

    def test_valid_board_parses_every_field(self) -> None:
        board = quarterdeck.parse_captain_board(json.dumps({
            "version": 1, "updated_at": "2026-10-08T10:00:00Z",
            "items": [board_item(detail="Short *detail*.", topic="Harbor", task="pier-pr")],
        }), Path("x"))
        self.assertEqual(board.warnings, [])
        self.assertEqual(board.updated_at, datetime(2026, 10, 8, 10, tzinfo=timezone.utc))
        item = board.items[0]
        self.assertEqual((item.id, item.group, item.title, item.topic, item.task), ("pier-merge", "merge", "Pier merge", "Harbor", "pier-pr"))
        self.assertEqual(item.links, [("Pier PR", PIER_PR)])

    def test_malformed_input_warns_without_crashing(self) -> None:
        for source in ("{not json", "[]", '{"version": 1, "updated_at": "2026-10-08T00:00:00Z"}'):
            board = quarterdeck.parse_captain_board(source, Path("x"))
            self.assertEqual(board.items, [])
            self.assertTrue(board.warnings)
        board = quarterdeck.parse_captain_board(json.dumps({
            "version": 1, "updated_at": "yesterday",
            "items": [
                board_item(),
                "nope",
                board_item(id="bad-group", group="shout"),
                board_item(id="no-ask", ask=""),
                board_item(id="pier-merge"),
                board_item(id="missing-task", task=None),
                board_item(id="odd-link", links=[{"label": "x", "url": "javascript:alert(1)"}], detail=7),
                board_item(id="bad-url", links=[{"label": "x", "url": "http://["}]),
            ],
        }), Path("x"))
        self.assertEqual([item.id for item in board.items], ["pier-merge", "odd-link", "bad-url"])
        self.assertEqual(board.items[1].links, [])
        self.assertEqual(board.items[2].links, [])
        self.assertIsNone(board.updated_at)
        text = "\n".join(board.warnings)
        for fragment in ("updated_at", "item 2", "bad-group", "no-ask", "task", "duplicate", "odd-link", "bad-url"):
            self.assertIn(fragment, text)

    def test_unsupported_versions_skip_items(self) -> None:
        for version in (2, True, 1.0):
            board = quarterdeck.parse_captain_board(json.dumps({
                "version": version, "items": [board_item()],
            }), Path("x"))
            self.assertFalse(board.supported)
            self.assertEqual(board.items, [])
            self.assertIn(f"unsupported version {version!r}", " ".join(board.warnings))

    def test_malformed_board_renders_warning_and_keeps_unsorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            homes, view = self.view(self.make_home(Path(temp), raw="{broken"))
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertIn("not valid JSON", page)
            self.assertEqual(len(view.unsorted), 3)
            self.assertIn("Raw note: choose spring or autumn.", page)

    def test_cross_check_hides_settled_and_lists_unsorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [
                board_item(),
                board_item(id="dock", title="Dock merge", task="dock-fix"),
                board_item(id="gone", title="Gone merge", task="no-such-task"),
                board_item(id="waiting", title="Vendor wait", task="waiting-work", group="forward"),
                board_item(id="note", title="Just a note", group="read", task="kelp-hold"),
                board_item(id="kelp", title="Kelp date", group="decide", task="kelp-hold"),
            ])
            homes, view = self.view(home)
            self.assertEqual(self.titles(view, "merge"), ["Pier merge"])
            self.assertEqual(self.titles(view, "forward"), [])
            self.assertEqual(self.titles(view, "read"), ["Just a note"])
            self.assertEqual(view.hidden, 3)
            self.assertEqual([entry.item.id for entry in view.unsorted], ["reef-hold"])
            self.assertEqual(view.unsorted[0].item.ask, "Raw note: red or green.")
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertNotIn("Dock merge", page)
            self.assertNotIn("Gone merge", page)
            self.assertNotIn("Vendor wait", page)
            self.assertIn("Not yet sorted", page)
            self.assertIn("Raw note: red or green.", page)
            board_section = page[page.index('id="board"'):page.index('id="reviews"')]
            self.assertNotIn("Raw note: waits on a vendor.", board_section)
            self.assertNotIn("Dock fix", board_section)

    def test_released_holds_are_settled_in_board_and_unsorted_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(
                id="released-board-item", title="Released board item", task="released-board-task",
            )])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "- [ ] released-board-task - Released board task (state: released) (held: yes) (hold-kind: captain)\n"
                "- [ ] released-unlisted-task - Released unlisted task (state: released) (held: yes) (hold-kind: captain)\n",
                encoding="utf-8",
            )
            homes, view = self.view(home)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(view.hidden, 1)
            self.assertEqual(view.groups["merge"], [])
            self.assertEqual(view.unsorted, [])

    def test_bullet_blocker_fields_survive_attributes_for_board_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(task="board-blocked-task")])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "- [ ] board-blocked-task - Board blocker (state: blocked) (blocked_by: internal-task) (waiting_on: captain)\n"
                "- [ ] hyphen-blocked-task - Hyphen blocker (state: blocked) (blocked-by: captain)\n"
                "- [ ] waiting-on-task - Waiting on blocker (state: blocked) (waiting_on: captain)\n"
                "- [ ] waiting-for-task - Waiting for blocker (state: blocked) (blocked_by: internal-task) (waiting_for: captain)\n"
                "- [ ] trailing-blocked-task - Trailing blocker (state: blocked) blocked-by: captain\n",
                encoding="utf-8",
            )
            homes, view = self.view(home)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(self.titles(view, "merge"), ["Pier merge"])
            self.assertEqual(
                [entry.item.id for entry in view.unsorted],
                ["hyphen-blocked-task", "waiting-on-task", "waiting-for-task", "trailing-blocked-task"],
            )

    def test_record_blocker_target_prioritizes_waiting_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(task="table-board-task")])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "| id | title | state | held | hold_kind | blocked_by | waiting_on | waiting_for |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| table-board-task | Board table blocker | blocked | yes | captain | internal-task | captain | |\n"
                "| table-unlisted-task | Unlisted table blocker | blocked | yes | captain | internal-task | | captain |\n",
                encoding="utf-8",
            )
            homes, view = self.view(home)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(self.titles(view, "merge"), ["Pier merge"])
            self.assertEqual([entry.item.id for entry in view.unsorted], ["table-unlisted-task"])

    def ago(self, **delta: float) -> str:
        return (self.NOW - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def page(self, homes, show_all: bool = False) -> str:
        return quarterdeck.build_html(
            homes, "Q", show_all=show_all, bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False),
        )

    def test_recency_bands_order_numbers_chips_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [
                board_item(id="r", group="read", title="Read one", task="kelp-hold", touched_at=self.ago(hours=2)),
                board_item(id="d", group="decide", title="Decide one", task="kelp-hold", topic="Alpha", touched_at=self.ago(hours=30)),
                board_item(id="a", group="approve", title="Approve one", task="kelp-hold", detail="More **context**.",
                           touched_at=self.ago(hours=10), project="harbor-demo",
                           brief={"url": "https://example.com/brief/approve", "recommendation": "Approve"}),
                board_item(id="m", touched_at=self.ago(hours=1)),
                board_item(id="w", group="decide", title="Week one", task="kelp-hold", touched_at=self.ago(days=3)),
                board_item(id="p", group="read", title="Parked one", task="kelp-hold", touched_at=self.ago(days=10)),
                board_item(id="o", group="forward", title="Old one", task="kelp-hold", touched_at=self.ago(days=20)),
            ])
            homes, _ = self.view(home)
            view = quarterdeck.board_view(homes, self.NOW)
            assert view is not None
            bands = {key: [e.item.title for e in entries] for key, entries in view.bands.items()}
            self.assertEqual(bands["desk"], ["Pier merge", "Approve one", "Decide one", "Read one"])
            self.assertEqual(bands["week"], ["Week one"])
            self.assertEqual(bands["parked"][:2], ["Old one", "Parked one"])
            self.assertEqual([e.item.title for e in view.bands["parked"] if e.sweep], ["Old one"])
            numbers = [e.number for key in quarterdeck.BOARD_BAND_KEYS for e in view.bands[key]]
            self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
            page = self.page(homes)
            order = [page.index(f'id="board-{key}"') for key in ("desk", "week", "parked")]
            self.assertEqual(order, sorted(order))
            self.assertIn('<details class="dashboard-section board-band" id="board-week" data-refresh-key="board-band:week">', page)
            self.assertIn('<details class="dashboard-section board-band" id="board-parked" data-refresh-key="board-band:parked">', page)
            self.assertIn("1 forward · 1 read · 1 not yet sorted.", page)
            self.assertIn("Weekly sweep: 1 parked item untouched for over 14 days. Keep, defer or drop?", page)
            self.assertIn('<span class="rec-label">Recommend</span> Approve</span>', page)
            self.assertIn('<a class="brief-link" href="https://example.com/brief/approve">Open brief</a>', page)
            self.assertIn('<span class="chip">harbor-demo</span>', page)
            self.assertIn('<span class="chip">Alpha</span>', page)
            self.assertIn("touched 2 hours ago", page)
            self.assertIn(f'<a href="{PIER_PR}">Pier PR</a>', page)
            self.assertIn("Not yet sorted", page)
            for entry in [*(entry for group in view.groups.values() for entry in group), *view.unsorted]:
                self.assertIn(f'data-refresh-key="{html.escape(entry.refresh_key, quote=True)}"', page)
            self.assertIn("<strong>context</strong>", page)
            self.assertLess(page.index('id="board"'), page.index('id="reviews"'))
            self.assertIn('<details class="dashboard-section reviews" id="reviews">', page)
            self.assertIn('width=device-width', page)
            everything = self.page(homes, show_all=True)
            self.assertIn('id="board-week" data-refresh-key="board-band:week" open>', everything)
            self.assertIn('id="board-parked" data-refresh-key="board-band:parked" open>', everything)

    def test_desk_shows_five_then_more_and_all_shows_everything(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [
                board_item(id=f"d{index}", group="decide", title=f"Desk {index}", task="kelp-hold",
                           touched_at=self.ago(hours=index)) for index in range(1, 8)
            ])
            homes, _ = self.view(home)
            page = self.page(homes)
            desk = page[page.index('id="board-desk"'):page.index('id="board-parked"')]
            more = desk.index("+2 more you touched recently")
            self.assertLess(desk.index("Desk 5"), more)
            self.assertGreater(desk.index("Desk 6"), more)
            self.assertNotIn("more you touched recently", self.page(homes, show_all=True))

    def test_empty_desk_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            homes, _ = self.view(self.make_home(Path(temp), [board_item(touched_at=self.ago(days=3))]))
            self.assertIn("Nothing touched in the last 2 days.", self.page(homes))

    @contextmanager
    def local_zone(self, zone: str):
        try:
            with patch.dict(os.environ, {"TZ": zone}):
                time.tzset()
                yield
        finally:
            time.tzset()

    def operator(self, last: str, started: str) -> dict[str, object]:
        return {"operator": {"last_active_at": last, "session_started_at": started}}

    def test_session_desk_groups_by_message_recency(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.local_zone("UTC"):
            home = self.make_home(Path(temp), [
                board_item(id="y", group="decide", title="Yesterday one", task="kelp-hold", touched_at=self.ago(hours=15)),
                board_item(id="h", group="decide", title="Hour one", task="kelp-hold", touched_at=self.ago(minutes=80)),
                board_item(id="f", group="read", title="Fresh one", task="kelp-hold", touched_at=self.ago(minutes=3)),
                board_item(id="b", group="decide", title="Before session", task="kelp-hold", touched_at=self.ago(hours=17)),
                board_item(id="n", group="decide", title="Minutes one", task="kelp-hold", touched_at=self.ago(minutes=20)),
                board_item(id="o", group="decide", title="Morning one", task="kelp-hold", touched_at=self.ago(hours=4)),
                board_item(id="m", touched_at=self.ago(minutes=6)),
            ], **self.operator(self.ago(minutes=5), self.ago(hours=16)))
            homes, _ = self.view(home)
            view = quarterdeck.board_view(homes, self.NOW)
            assert view is not None and view.session is not None
            self.assertFalse(view.session.idle)
            self.assertEqual(
                [(heading, [e.item.title for e in entries]) for heading, entries in view.desk_groups],
                [
                    ("Open items from your last message", ["Fresh one"]),
                    ("Still waiting on you from 6 minutes ago", ["Pier merge", "Minutes one"]),
                    ("Open items from an hour ago", ["Hour one"]),
                    ("Open items from this morning", ["Morning one"]),
                    ("Open items from yesterday", ["Yesterday one"]),
                ],
            )
            self.assertEqual([e.item.title for e in view.bands["week"]], ["Before session"])
            numbers = [e.number for key in quarterdeck.BOARD_BAND_KEYS for e in view.bands[key]]
            self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
            self.assertEqual([e.item.title for e in view.bands["desk"]][:2], ["Fresh one", "Pier merge"])
            page = self.page(homes)
            desk = page[page.index('id="board-desk"'):page.index('id="board-week"')]
            self.assertIn("Raised or discussed in this session with Firstmate, which began 16 hours ago.", desk)
            headings = [desk.index(f'<h3 class="desk-group">{heading}</h3>') for heading, _ in view.desk_groups]
            self.assertEqual(headings, sorted(headings))
            self.assertNotIn("more you touched recently", desk)
            self.assertIn('id="board-week" data-refresh-key="board-band:week">', page)

    def test_day_parts_follow_local_time(self) -> None:
        session = quarterdeck.DeskSession(self.NOW, self.NOW - timedelta(days=3), False)
        with self.local_zone("Asia/Manila"):  # NOW is 20:00 local
            self.assertEqual(quarterdeck.desk_bucket(self.NOW - timedelta(hours=3), session, self.NOW)[1],
                             "Open items from this evening")
            self.assertEqual(quarterdeck.desk_bucket(self.NOW - timedelta(hours=5), session, self.NOW)[1],
                             "Open items from this afternoon")
            self.assertEqual(quarterdeck.desk_bucket(self.NOW - timedelta(hours=13), session, self.NOW)[1],
                             "Open items from this morning")
            self.assertEqual(quarterdeck.desk_bucket(self.NOW - timedelta(hours=21), session, self.NOW)[1],
                             "Open items from yesterday")
            self.assertEqual(quarterdeck.desk_bucket(self.NOW - timedelta(hours=50), session, self.NOW)[1],
                             "Open items from earlier this session")

    def test_idle_desk_leads_with_last_session_then_everything_else(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.local_zone("UTC"):
            home = self.make_home(Path(temp), [
                board_item(id="s", group="decide", title="Session one", task="kelp-hold", touched_at=self.ago(hours=7)),
                board_item(id="m", touched_at=self.ago(hours=5)),
                board_item(id="w", group="decide", title="Earlier one", task="kelp-hold", touched_at=self.ago(hours=9)),
            ], **self.operator(self.ago(hours=6), self.ago(hours=8)))
            homes, _ = self.view(home)
            view = quarterdeck.board_view(homes, self.NOW)
            assert view is not None and view.session is not None
            self.assertTrue(view.session.idle)
            self.assertEqual([e.item.title for e in view.bands["desk"]], ["Pier merge", "Session one"])
            self.assertEqual([e.item.title for e in view.bands["week"]], ["Earlier one"])
            page = self.page(homes)
            self.assertIn("<h2>Open items from our last session</h2>", page)
            self.assertIn("Your last message to Firstmate was 6 hours ago.", page)
            self.assertNotIn('class="desk-group"', page)
            self.assertIn('id="board-week" data-refresh-key="board-band:week" open>', page)
            self.assertIn('id="board-parked" data-refresh-key="board-band:parked">', page)

    def test_new_session_leaves_earlier_work_off_the_desk(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.local_zone("UTC"):
            home = self.make_home(Path(temp), [board_item(touched_at=self.ago(hours=20))],
                                  **self.operator(self.ago(minutes=1), self.ago(minutes=1)))
            homes, _ = self.view(home)
            view = quarterdeck.board_view(homes, self.NOW)
            assert view is not None
            self.assertEqual(view.bands["desk"], [])
            self.assertEqual([e.item.title for e in view.bands["week"]], ["Pier merge"])
            self.assertIn("Nothing from this session is waiting on you. Earlier work is below.", self.page(homes))

    def test_session_desk_uses_conversation_touches_not_backlog_recency(self) -> None:
        for idle in (False, True):
            with self.subTest(idle=idle), tempfile.TemporaryDirectory() as temp, self.local_zone("UTC"):
                home = self.make_home(Path(temp), [
                    board_item(id="old", task="kelp-hold", touched_at=self.ago(hours=10)),
                    board_item(id="untouched", group="decide", task="reef-hold"),
                    board_item(id="discussed", group="read", touched_at=self.ago(hours=7)),
                ], **self.operator(self.ago(hours=6) if idle else self.ago(minutes=5), self.ago(hours=8)))
                (home / "data" / "backlog.md").write_text(
                    BOARD_BACKLOG.replace(
                        "(hold-kind: captain)", f"(hold-kind: captain) (hold-set: {self.ago(minutes=1)})",
                    ).replace("(review_ready: yes)", f"(review_ready: yes) (hold-set: {self.ago(minutes=1)})")
                    + "\n## Queued\n\n"
                    "- [ ] new-hold - New hold (held: yes) (hold: pick) (hold-kind: captain)\n"
                    f"  Captain hold set: {self.ago(minutes=1)}\n",
                    encoding="utf-8",
                )
                homes, _ = self.view(home)
                view = quarterdeck.board_view(homes, self.NOW)
                assert view is not None
                self.assertEqual([e.item.id for e in view.bands["desk"]], ["discussed"])
                self.assertEqual(view.bands["desk"][0].touched, quarterdeck.parse_board_time(self.ago(hours=7)))
                self.assertEqual({e.item.id for e in view.bands["week"]}, {"old", "untouched", "new-hold"})
                self.assertTrue(all(e.touched == quarterdeck.parse_board_time(self.ago(minutes=1))
                                    for e in view.bands["week"]))
                self.assertEqual(view.desk_groups[0][0], "" if idle else "Open items from this morning")

    def test_session_and_message_boundaries_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp, self.local_zone("UTC"):
            home = self.make_home(Path(temp), [
                board_item(id="before-session", touched_at=self.ago(minutes=31)),
                board_item(id="at-session", touched_at=self.ago(minutes=30)),
                board_item(id="before-message", touched_at=self.ago(minutes=6)),
                board_item(id="at-message", touched_at=self.ago(minutes=5)),
            ], **self.operator(self.ago(minutes=5), self.ago(minutes=30)))
            homes, _ = self.view(home)
            view = quarterdeck.board_view(homes, self.NOW)
            assert view is not None
            self.assertEqual([(heading, [e.item.id for e in entries]) for heading, entries in view.desk_groups], [
                ("Open items from your last message", ["at-message"]),
                ("Still waiting on you from 6 minutes ago", ["before-message", "at-session"]),
            ])
            self.assertEqual([e.item.id for e in view.bands["week"]], ["before-session"])

    def test_newest_operator_activity_across_homes_wins(self) -> None:
        older = quarterdeck.CaptainBoard(Path("a"), last_active_at=self.NOW - timedelta(hours=9),
                                         session_started_at=self.NOW - timedelta(hours=10))
        newer = quarterdeck.CaptainBoard(Path("b"), last_active_at=self.NOW + timedelta(hours=1),
                                         session_started_at=self.NOW - timedelta(hours=1))
        session = quarterdeck.desk_session([older, newer, quarterdeck.CaptainBoard(Path("c"))], self.NOW)
        self.assertEqual(session, quarterdeck.DeskSession(self.NOW, self.NOW - timedelta(hours=1), False))
        self.assertIsNone(quarterdeck.desk_session([quarterdeck.CaptainBoard(Path("c"))], self.NOW))

    def test_invalid_operator_record_warns_and_uses_48_hour_rule(self) -> None:
        for operator in ("soon", {"last_active_at": "soon"}, {}):
            board = quarterdeck.parse_captain_board(
                json.dumps({"version": 1, "updated_at": "2026-10-08T00:00:00Z", "items": [], "operator": operator}), Path("b"),
            )
            self.assertIsNone(board.last_active_at, operator)
            self.assertTrue(any("operator needs an ISO 8601 last_active_at" in w for w in board.warnings), operator)
        board = quarterdeck.parse_captain_board(json.dumps({
            "version": 1, "updated_at": "2026-10-08T00:00:00Z", "items": [],
            "operator": {"last_active_at": "2026-10-08T10:00:00Z", "session_started_at": "later"},
        }), Path("b"))
        self.assertEqual(board.session_started_at, board.last_active_at)
        self.assertTrue(any("session_started_at must be an ISO 8601 time" in w for w in board.warnings))
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(touched_at=self.ago(hours=20))], operator="soon")
            homes, _ = self.view(home)
            view = quarterdeck.board_view(homes, self.NOW)
            assert view is not None
            self.assertIsNone(view.session)
            self.assertEqual([e.item.title for e in view.bands["desk"]], ["Pier merge"])

    def test_backlog_fallbacks_for_touched_time_deferral_and_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [
                board_item(id="noted", group="decide", title="Noted", task="noted-task"),
                board_item(id="newer", group="decide", title="Newer", task="hold-set-task", touched_at=self.ago(hours=3)),
            ])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "- [ ] noted-task - Noted (repo: reef-demo) (held: yes) (hold: pick one) (hold-kind: captain)\n"
                "  Captain 2026-09-30: first. Captain 2026-10-07: later.\n"
                "- [ ] hold-set-task - Hold set (held: yes) (hold: pick) (hold-kind: captain)\n"
                "  Captain hold set: 2026-10-04T08:00:00Z\n"
                "- [ ] week-task - Week (held: yes) (hold: pick) (hold-kind: captain)\n"
                "  Captain hold set: 2026-10-04T08:00:00Z\n"
                "- [ ] until-task - Until (held: yes) (hold: later) (hold-kind: captain) (hold-until: 2026-10-20)\n"
                "  Captain 2026-10-08: asked to wait.\n"
                "- [ ] future-task - Future (held: yes) (hold: check in) (hold-kind: captain)\n"
                "  Captain 2026-10-08: agreed. Captain 2026-10-19: check-in.\n"
                "- [ ] waits-task - Waits (held: yes) (hold: waits on harbor-chart to land) (hold-kind: captain)\n"
                "  Captain 2026-10-08: noted.\n"
                "- [ ] silent-task - Silent (held: yes) (hold: pick) (hold-kind: captain)\n"
                "- [ ] harbor-chart - Chart dependency\n",
                encoding="utf-8",
            )
            homes, _ = self.view(home)
            view = quarterdeck.board_view(homes, self.NOW)
            assert view is not None
            entries = {e.item.task: e for key in quarterdeck.BOARD_BAND_KEYS for e in view.bands[key]}
            self.assertEqual(entries["noted-task"].touched, datetime(2026, 10, 7, tzinfo=timezone.utc))
            self.assertEqual(entries["noted-task"].band, "desk")
            self.assertEqual(entries["noted-task"].item.project, "reef-demo")
            self.assertEqual(entries["hold-set-task"].band, "desk")
            self.assertEqual(entries["week-task"].band, "week")
            for task, reason in (("until-task", "deferred until 2026-10-20"),
                                 ("waits-task", "deferred by its hold note")):
                self.assertEqual((entries[task].band, entries[task].deferred), ("parked", reason))
                self.assertFalse(entries[task].sweep)
            self.assertEqual(entries["future-task"].touched, datetime(2026, 10, 8, tzinfo=timezone.utc))
            self.assertEqual((entries["future-task"].band, entries["future-task"].deferred), ("desk", ""))
            self.assertEqual((entries["silent-task"].band, entries["silent-task"].sweep), ("parked", False))
            self.assertIn("touched time unknown", self.page(homes))

    def test_encoded_hold_reasons_are_decoded_for_display_and_deferral(self) -> None:
        encoded = "fm-hold-v1:" + base64.b64encode("Waits on chart to land.".encode()).decode()
        self.assertEqual(quarterdeck.decode_hold_reason(encoded), "Waits on chart to land.")
        for raw in ("fm-hold-v1:not base64!", "fm-hold-v1:" + base64.b64encode(b"\xff\xfe").decode(), "plain note"):
            self.assertEqual(quarterdeck.decode_hold_reason(raw), raw)
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                f"- [ ] coded-task - Coded (held: yes) (hold: {encoded}) (hold-kind: captain)\n"
                "  Captain 2026-10-08: asked.\n"
                "- [ ] chart - Chart dependency\n",
                encoding="utf-8",
            )
            homes, _ = self.view(home)
            view = quarterdeck.board_view(homes, self.NOW)
            assert view is not None
            entry = view.unsorted[0]
            self.assertEqual(entry.item.ask, "Waits on chart to land.")
            self.assertEqual((entry.band, entry.deferred), ("parked", "deferred by its hold note"))
            self.assertNotIn("fm-hold-v1:", self.page(homes))

    def test_hold_timing_and_dependencies_across_backlog_formats(self) -> None:
        cases = [
            ("hold-until", "2026-10-20", "pick", "parked", datetime(2026, 10, 8, tzinfo=timezone.utc)),
            ("hold-set", "2026-10-08T08:00:00Z", "pick", "desk", datetime(2026, 10, 8, 8, tzinfo=timezone.utc)),
            *[("hold-set", "2026-10-08T08:00:00Z", f"waits on {identifier} to land", "parked",
               datetime(2026, 10, 8, 8, tzinfo=timezone.utc))
              for identifier in ("chart", "harbor-chart", "harbor_chart", "harbor.chart")],
            ("hold-set", "2026-10-08T08:00:00Z", "waits on captain to choose", "desk",
             datetime(2026, 10, 8, 8, tzinfo=timezone.utc)),
            *[("hold-set", "2026-10-08T08:00:00Z", reason, "desk",
               datetime(2026, 10, 8, 8, tzinfo=timezone.utc))
              for reason in ("Waiting on the captain to approve", "waiting for the captain's decision",
                             "waits on your approval", "waiting for a captain to choose", "waiting on me",
                             "Waiting for approval from the captain", "waits on unknown-task to land",
                             "after chart-ask", "waiting on chart-ask", "waiting on Chart",
                             "Previously parked; now ready for captain approval", "Deferred", "parked")],
            ("hold-set", "2026-10-08T08:00:00Z", "waiting on the chart to land", "desk",
             datetime(2026, 10, 8, 8, tzinfo=timezone.utc)),
            ("hold-set", "2026-10-08T08:00:00Z", "parked label to approve", "desk",
             datetime(2026, 10, 8, 8, tzinfo=timezone.utc)),
        ]
        cases += [
            ("hold-set", "2026-10-08T08:00:00Z", reason, "parked",
             datetime(2026, 10, 8, 8, tzinfo=timezone.utc))
            for reason in ("Deferred until later", "Deferred until the chart work settles",
                           "Hold off releasing until the chart work settles", "Hold off v1.2 until the chart work settles",
                           "waits until the chart work settles", "Waits on `chart` to land",
                           "after chart.", "waiting for chart,",
                           "waiting on unknown-task; after harbor.chart!")
        ]
        cases += [
            (field, value, "fm-hold-v1:" + base64.b64encode(reason.encode()).decode(), band, touched)
            for field, value, reason, band, touched in cases if field == "hold-set"
        ]
        for field, value, reason, band, touched in cases:
            for table in (False, True):
                for boarded in (False, True):
                    with self.subTest(field=field, reason=reason, table=table, boarded=boarded), tempfile.TemporaryDirectory() as temp:
                        home = self.make_home(Path(temp), [board_item(task="chart-ask")] if boarded else [])
                        title = "Chart ask Captain 2026-10-08" if field == "hold-until" else "Chart ask"
                        if table:
                            backlog = (
                                f"| id | title | held | hold_kind | hold_reason | {field.replace('-', '_')} |\n"
                                "| --- | --- | --- | --- | --- | --- |\n"
                                f"| chart-ask | {title} | yes | captain | {reason} | {value} |\n"
                            )
                        else:
                            backlog = f"- [ ] chart-ask - {title} (held: yes) (hold-kind: captain) (hold: {reason}) ({field}: {value})\n"
                        backlog += "".join(
                            f"| {identifier} | Dependency | no | | | |\n" if table
                            else f"- [ ] {identifier} - Dependency\n"
                            for identifier in ("chart", "harbor-chart", "harbor_chart", "harbor.chart")
                        )
                        (home / "data" / "backlog.md").write_text("# Backlog\n\n## Queued\n\n" + backlog, encoding="utf-8")
                        homes, _ = self.view(home)
                        view = quarterdeck.board_view(homes, self.NOW)
                        assert view is not None
                        entry = next(e for entries in view.bands.values() for e in entries if e.item.task == "chart-ask")
                        self.assertEqual((entry.band, entry.touched), (band, touched))
                        if field == "hold-until":
                            self.assertEqual(entry.deferred, "deferred until 2026-10-20")

    def test_dependencies_stay_within_target_home_for_routed_and_unsorted_items(self) -> None:
        for dependency_home in ("parent", "child"):
            with self.subTest(dependency_home=dependency_home), tempfile.TemporaryDirectory() as temp:
                home = self.make_home(Path(temp), [board_item(task="wren/chart-ask")])
                child = self.make_home(Path(temp) / "child-root")
                (home / "data" / "secondmates.md").write_text(
                    f"- wren - Keeps the guide (home: {child}; scope: Guide)\n", encoding="utf-8",
                )
                dependency = "- [x] chart - Chart dependency\n"
                for target, identifiers in ((home, ("parent-ask",)), (child, ("chart-ask", "unlisted-ask"))):
                    backlog = "# Backlog\n\n## Queued\n\n" + "".join(
                        f"- [ ] {identifier} - Chart ask (held: yes) (hold-kind: captain) "
                        "(hold: waits on chart to land) (hold-set: 2026-10-08T08:00:00Z)\n"
                        for identifier in identifiers
                    )
                    if target == (home if dependency_home == "parent" else child):
                        backlog += dependency
                    (target / "data" / "backlog.md").write_text(backlog, encoding="utf-8")
                homes, _ = self.view(home)
                view = quarterdeck.board_view(homes, self.NOW)
                assert view is not None
                entries = {e.item.task: e for band in view.bands.values() for e in band}
                child_band = "parked" if dependency_home == "child" else "desk"
                self.assertEqual(entries["wren/chart-ask"].band, child_band)
                self.assertEqual(entries["unlisted-ask"].band, child_band)
                self.assertEqual(entries["parent-ask"].band, "parked" if dependency_home == "parent" else "desk")

    def test_full_iso_hold_timestamps_determine_recency_and_deferral(self) -> None:
        now = datetime(2026, 10, 10, 4, tzinfo=timezone.utc)
        hold_set_cases = [
            ("2026-10-08T08:00:00.500+08:00", datetime(2026, 10, 8, 0, 0, 0, 500000, tzinfo=timezone.utc), "week"),
            ("2026-10-08T00:00:00.500-08:00", datetime(2026, 10, 8, 8, 0, 0, 500000, tzinfo=timezone.utc), "desk"),
            ("2026-10-08T08:00:00.500Z", datetime(2026, 10, 8, 8, 0, 0, 500000, tzinfo=timezone.utc), "desk"),
            ("2026-10-10T04:00:00.500Z", None, "parked"),
            ("2026-10-08T08:00:00.500+0800", datetime(2026, 10, 8, 0, 0, 0, 500000, tzinfo=timezone.utc), "week"),
            ("2026-10-08 08:00:00.500+08:00", datetime(2026, 10, 8, 0, 0, 0, 500000, tzinfo=timezone.utc), "week"),
        ]
        hold_until_cases = [
            ("2026-10-10T08:00:00.500+08:00", now, "desk"),
            ("2026-10-09T22:00:00.500-08:00", now, "parked"),
            ("2026-10-10T04:00:00.500Z", now, "parked"),
        ]
        for field, cases, formats in (("hold-set", hold_set_cases, ("body", "bullet", "table")),
                                      ("hold-until", hold_until_cases, ("bullet", "table"))):
            for value, touched, band in cases:
                for source_format in formats:
                    for boarded in (False, True):
                        with self.subTest(field=field, value=value, source_format=source_format, boarded=boarded), tempfile.TemporaryDirectory() as temp:
                            home = self.make_home(Path(temp), [board_item(task="chart-ask")] if boarded else [])
                            if source_format == "table":
                                backlog = (
                                    f"| id | title | held | hold_kind | {field.replace('-', '_')} |\n"
                                    "| --- | --- | --- | --- | --- |\n"
                                    f"| chart-ask | Chart ask | yes | captain | {value} |\n"
                                )
                                if field == "hold-until":
                                    backlog = backlog.replace("hold_until |", "hold_until | hold_set |").replace(
                                        "| --- | --- | --- | --- | --- |", "| --- | --- | --- | --- | --- | --- |"
                                    ).replace(f"| {value} |", f"| {value} | {now.isoformat()} |")
                            else:
                                backlog = "- [ ] chart-ask - Chart ask (held: yes) (hold-kind: captain)"
                                backlog += f" ({field}: {value})\n" if source_format == "bullet" else f"\n  Captain hold set: {value}\n"
                                if field == "hold-until":
                                    backlog += f"  Captain hold set: {now.isoformat()}\n"
                            (home / "data" / "backlog.md").write_text("# Backlog\n\n## Queued\n\n" + backlog, encoding="utf-8")
                            homes, _ = self.view(home)
                            view = quarterdeck.board_view(homes, now)
                            assert view is not None
                            entry = next(e for entries in view.bands.values() for e in entries if e.item.task == "chart-ask")
                            self.assertEqual((entry.touched, entry.band), (touched, band))
                            self.assertEqual(bool(entry.deferred), field == "hold-until" and band == "parked")

    def test_age_displays_use_parsed_offset_and_fractional_timestamps(self) -> None:
        now = datetime(2026, 10, 10, 2, tzinfo=timezone.utc)
        cases = [
            ("2026-10-09T08:00:00.500+08:00", "1 day"),
            ("2026-10-09T08:00:00.500-08:00", "9 hours"),
            ("2026-10-09T08:00:00.500Z", "17 hours"),
            ("2026-10-10T09:30:00.500+08:00", "under an hour"),
        ]
        for value, age in cases:
            for source_format in ("body", "bullet", "table", "board"):
                for boarded in (False, True) if source_format != "board" else (True,):
                    with self.subTest(value=value, source_format=source_format, boarded=boarded), tempfile.TemporaryDirectory() as temp:
                        items = [board_item(task="chart-ask", **({"touched_at": value} if source_format == "board" else {}))] if boarded else []
                        home = self.make_home(Path(temp), items)
                        if source_format == "table":
                            backlog = (
                                "| id | title | held | hold_kind | hold_set |\n"
                                "| --- | --- | --- | --- | --- |\n"
                                f"| chart-ask | Chart ask | yes | captain | {value} |\n"
                            )
                        else:
                            backlog = "- [ ] chart-ask - Chart ask (held: yes) (hold-kind: captain)"
                            if source_format == "bullet":
                                backlog += f" (hold-set: {value})"
                            backlog += "\n"
                            if source_format == "body":
                                backlog += f"  Captain hold set: {value}\n"
                        (home / "data" / "backlog.md").write_text("# Backlog\n\n## Queued\n\n" + backlog, encoding="utf-8")
                        homes, _ = self.view(home)
                        view = quarterdeck.board_view(homes, now)
                        assert view is not None
                        entry = next(e for entries in view.bands.values() for e in entries if e.item.task == "chart-ask")
                        self.assertIn(f'<span class="chip chip-age">touched {age} ago</span>', quarterdeck.board_entry_html(entry, False, now))
            updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
            self.assertEqual(quarterdeck.board_age_text(updated, now), f"{age} ago")
        future = datetime(2026, 10, 11, 8, 0, 0, 500000, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(quarterdeck.board_age_text(future, now), "22 hours ahead (clock skew)")

    def test_structured_dependencies_defer_across_backlog_formats(self) -> None:
        for field in ("blocked_by", "blocked-by", "waiting_on", "waiting-on", "waiting_for", "waiting-for"):
            for dependency in ("chart", "chart-ask", "captain", "unknown-task"):
                for table in (False, True):
                    for boarded in (False, True):
                        with self.subTest(field=field, dependency=dependency, table=table, boarded=boarded), tempfile.TemporaryDirectory() as temp:
                            home = self.make_home(Path(temp), [board_item(task="chart-ask")] if boarded else [])
                            if table:
                                backlog = (
                                    f"| id | title | held | hold_kind | hold_set | {field} |\n"
                                    "| --- | --- | --- | --- | --- | --- |\n"
                                    f"| chart-ask | Chart ask | yes | captain | 2026-10-08T08:00:00Z | {dependency} |\n"
                                    "| chart | Dependency | no | | | |\n"
                                )
                            else:
                                backlog = (
                                    "- [ ] chart-ask - Chart ask (held: yes) (hold-kind: captain) "
                                    f"(hold-set: 2026-10-08T08:00:00Z) ({field}: {dependency})\n"
                                    "- [ ] chart - Dependency\n"
                                )
                            (home / "data" / "backlog.md").write_text("# Backlog\n\n## Queued\n\n" + backlog, encoding="utf-8")
                            homes, _ = self.view(home)
                            view = quarterdeck.board_view(homes, self.NOW)
                            assert view is not None
                            entry = next(e for entries in view.bands.values() for e in entries if e.item.task == "chart-ask")
                            self.assertEqual(entry.band, "parked" if dependency == "chart" else "desk")
        for table in (False, True):
            with self.subTest(multiple_fields=True, table=table), tempfile.TemporaryDirectory() as temp:
                home = self.make_home(Path(temp), [])
                backlog = (
                    "| id | title | held | hold_kind | hold_set | blocked_by | waiting_on |\n"
                    "| --- | --- | --- | --- | --- | --- | --- |\n"
                    "| chart-ask | Chart ask | yes | captain | 2026-10-08T08:00:00Z | chart | captain |\n"
                    "| chart | Dependency | no | | | | |\n"
                ) if table else (
                    "- [ ] chart-ask - Chart ask (held: yes) (hold-kind: captain) "
                    "(hold-set: 2026-10-08T08:00:00Z) (blocked_by: chart) (waiting_on: captain)\n"
                    "- [ ] chart - Dependency\n"
                )
                (home / "data" / "backlog.md").write_text("# Backlog\n\n## Queued\n\n" + backlog, encoding="utf-8")
                homes, _ = self.view(home)
                view = quarterdeck.board_view(homes, self.NOW)
                assert view is not None
                self.assertEqual(view.unsorted[0].band, "parked")

    def test_table_hold_reason_fields_normalize_decoded_markdown(self) -> None:
        reason = "Waits on `chart` to land"
        encoded = "fm-hold-v1:" + base64.b64encode(reason.encode()).decode()
        for field in ("hold_reason", "blocked_reason", "block_reason"):
            for value in (reason, encoded):
                for boarded in (False, True):
                    with self.subTest(field=field, value=value, boarded=boarded), tempfile.TemporaryDirectory() as temp:
                        home = self.make_home(Path(temp), [board_item(task="chart-ask")] if boarded else [])
                        (home / "data" / "backlog.md").write_text(
                            "# Backlog\n\n## Queued\n\n"
                            f"| id | title | held | hold_kind | hold_set | {field} |\n"
                            "| --- | --- | --- | --- | --- | --- |\n"
                            f"| chart-ask | Chart ask | yes | captain | 2026-10-08T08:00:00Z | {value} |\n"
                            "| chart | Dependency | no | | | |\n", encoding="utf-8",
                        )
                        homes, _ = self.view(home)
                        self.assertEqual(quarterdeck.backlog_items(homes[0].backlog_markdown, homes[0])[0].hold_reason,
                                         "Waits on chart to land")
                        view = quarterdeck.board_view(homes, self.NOW)
                        assert view is not None
                        entry = next(e for entries in view.bands.values() for e in entries if e.item.task == "chart-ask")
                        self.assertEqual((entry.band, entry.deferred), ("parked", "deferred by its hold note"))

    def test_project_metadata_survives_backlog_conversion(self) -> None:
        for table in (False, True):
            for boarded in (False, True):
                with self.subTest(table=table, boarded=boarded), tempfile.TemporaryDirectory() as temp:
                    home = self.make_home(Path(temp), [board_item(task="chart-ask")] if boarded else [])
                    backlog = (
                        "| id | title | repo | held | hold_kind | hold_reason | hold_set |\n"
                        "| --- | --- | --- | --- | --- | --- | --- |\n"
                        "| chart-ask | Chart ask | reef-demo | yes | captain | choose | 2026-10-08T08:00:00Z |\n"
                    ) if table else (
                        "- [ ] chart-ask - Chart ask (repo: reef-demo) (held: yes) (hold-kind: captain) "
                        "(hold: choose) (hold-set: 2026-10-08T08:00:00Z)\n"
                    )
                    (home / "data" / "backlog.md").write_text("# Backlog\n\n## Queued\n\n" + backlog, encoding="utf-8")
                    homes, _ = self.view(home)
                    view = quarterdeck.board_view(homes, self.NOW)
                    assert view is not None
                    entry = next(e for entries in view.bands.values() for e in entries if e.item.task == "chart-ask")
                    self.assertEqual(entry.item.project, "reef-demo")
                    self.assertIn('<span class="chip">reef-demo</span>', self.page(homes))

    def test_new_optional_fields_parse_and_warn(self) -> None:
        board = quarterdeck.parse_captain_board(json.dumps({
            "version": 1, "updated_at": "2026-10-08T10:00:00Z",
            "items": [
                board_item(touched_at="2026-10-08T09:00:00Z", project="pier-demo",
                           brief={"url": "https://example.com/brief", "recommendation": "Merge"}, extra="kept"),
                board_item(id="bad", touched_at="soon", brief={"url": "ftp://x", "recommendation": 3}),
                board_item(id="worse", brief="https://example.com"),
            ],
        }), Path("x"))
        first, bad, worse = board.items
        self.assertEqual(first.touched_at, datetime(2026, 10, 8, 9, tzinfo=timezone.utc))
        self.assertEqual((first.project, first.brief_url, first.recommendation), ("pier-demo", "https://example.com/brief", "Merge"))
        self.assertEqual((bad.touched_at, bad.brief_url, bad.recommendation), (None, "", ""))
        self.assertEqual(worse.brief_url, "")
        text = "\n".join(board.warnings)
        for fragment in ("(bad): touched_at", "(bad): brief url", "(bad): brief recommendation", "(worse): brief must be"):
            self.assertIn(fragment, text)

    def test_board_age_and_stale_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item()], updated_at="2026-10-08T09:00:00Z")
            backlog = home / "data" / "backlog.md"
            for when, stale in (("2026-10-08T08:00:00Z", False), ("2026-10-08T10:00:00Z", True)):
                stamp = datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
                os.utime(backlog, (stamp, stamp))
                homes, view = self.view(home)
                page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
                self.assertEqual(bool(view.status[0][2]), stale)
                self.assertEqual("Board may be out of date" in page, stale)
                self.assertIn("Board updated", page)

    def test_future_board_timestamp_reports_clock_skew(self) -> None:
        recent_past = datetime.fromtimestamp(self.NOW.timestamp() - 60, timezone.utc)
        near_future = datetime.fromtimestamp(self.NOW.timestamp() + 60, timezone.utc)
        later_future = datetime.fromtimestamp(self.NOW.timestamp() + 180, timezone.utc)
        self.assertEqual(quarterdeck.board_age_text(recent_past, self.NOW), "just now")
        for future in (near_future, later_future):
            label = quarterdeck.board_age_text(future, self.NOW)
            self.assertIn("clock skew", label)
            self.assertIn("ahead", label)
            self.assertNotIn("ago", label)

    def test_refresh_restores_open_disclosures_and_continues_on_storage_failure(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not available to execute the generated refresh script")
        page = quarterdeck.build_html([], "Q", refresh_seconds=30)
        refresh_script = re.findall(r"<script>(.*?)</script>", page, flags=re.S)[-1]
        harness = r"""
const storageKey = "quarterdeck-open-details";
const details = [
  { id: "item-9", dataset: { refreshKey: "/home/data/captain-board.json:merge" }, open: false },
  { id: "reviews", dataset: {}, open: false },
  { id: "", dataset: { refreshKey: "bearing:backlog-task-1.html" }, open: false }
];
const values = new Map([[storageKey, JSON.stringify([
  "/home/data/captain-board.json:merge", "reviews", "bearing:backlog-task-1.html"
])]]);
let reloaded = false;
globalThis.document = {
  querySelectorAll(selector) {
    if (selector === "details") return details;
    if (selector === "details[open]") return details.filter(detail => detail.open);
    throw new Error("unexpected selector " + selector);
  }
};
globalThis.sessionStorage = {
  getItem(key) { return values.get(key) || null; },
  setItem(key, value) { values.set(key, value); },
  removeItem(key) { values.delete(key); }
};
globalThis.location = { reload() { reloaded = true; } };
globalThis.setTimeout = (callback, delay) => {
  if (delay !== 30000) throw new Error("refresh cadence changed");
  callback();
};
"""
        verify = r"""
if (!details.every(detail => detail.open)) throw new Error("open disclosures were not restored");
if (!reloaded) throw new Error("page did not reload");
const saved = JSON.parse(values.get(storageKey));
if (saved.length !== 3) throw new Error("open disclosures were not saved");
"""
        result = subprocess.run(
            [node, "-e", harness + refresh_script + verify],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        no_storage = r"""
let reloaded = false;
globalThis.document = { querySelectorAll() { return []; } };
globalThis.sessionStorage = new Proxy({}, { get() { throw new Error("storage unavailable"); } });
globalThis.location = { reload() { reloaded = true; } };
globalThis.setTimeout = callback => callback();
"""
        unavailable_verify = "if (!reloaded) throw new Error('reload stopped when storage was unavailable');"
        result = subprocess.run(
            [node, "-e", no_storage + refresh_script + unavailable_verify],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bearing_task_details_have_stable_refresh_keys(self) -> None:
        item = quarterdeck.BItem(
            title="Example task", owner="home", item_id="task-1", body="Task details.",
        )
        rendered = quarterdeck.bearings_item_html(item, self.NOW, "call")
        self.assertIn('data-refresh-key="bearing:home:task-1"', rendered)

    def test_board_text_is_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(title="<script>x</script>", task="kelp-hold")])
            homes, _ = self.view(home)
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertNotIn("<script>x</script>", page)

    def test_tasks_axi_hold_shape_without_held_flag_is_still_waiting_on_captain(self) -> None:
        # tasks-axi writes (hold: reason) (hold-kind: captain) and no (held: yes).
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(id="reef-item", task="reef-approval", links=[])])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "- [ ] reef-approval - Approve the reef plan (repo: reef-demo) (kind: captain) (since 2026-10-07) "
                "(hold: Plan needs captain word) (hold-kind: captain)\n"
                "- [ ] reef-unboarded - Pick a buoy (kind: captain) (hold: Red or green) (hold-kind: captain)\n"
                "- [ ] reef-vendor - Await vendor quote (hold: Vendor owes a quote) (hold-kind: external)\n"
                "- [ ] reef-placeholder - No real hold (hold: -) (hold-kind: captain)\n",
                encoding="utf-8",
            )
            homes, view = self.view(home)
            self.assertEqual(view.hidden, 0)
            self.assertEqual(self.titles(view, "merge"), ["Pier merge"])
            self.assertEqual([entry.item.id for entry in view.unsorted], ["reef-unboarded"])
            bearings = quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False)
            self.assertEqual({item.item_id for item in bearings.call}, {"reef-approval", "reef-unboarded"})

    def test_unsorted_covers_unboarded_roots_and_secondmates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            board_home, other_home = root / "A", root / "B"
            child_home, child_board_home = root / "B-child", root / "B-board-child"

            def write_held(home: Path, task_id: str, title: str) -> None:
                data = home / "data"
                data.mkdir(parents=True)
                (data / "backlog.md").write_text(
                    f"# Backlog\n\n## Queued\n\n- [ ] {task_id} - {title} (held: yes) (hold: Raw note: {title}.) (hold-kind: captain)\n",
                    encoding="utf-8",
                )

            write_held(board_home, "anchor", "Anchor task")
            (board_home / "data" / "captain-board.json").write_text(
                json.dumps({"version": 1, "updated_at": "2026-10-08T10:00:00Z", "items": [board_item(task="anchor", links=[])]}),
                encoding="utf-8",
            )
            write_held(other_home, "root-hold", "Unboarded root hold")
            write_held(child_home, "child-hold", "Unboarded child hold")
            write_held(child_board_home, "child-anchor", "Child board task")
            (child_board_home / "data" / "captain-board.json").write_text(
                json.dumps({"version": 1, "updated_at": "2026-10-08T10:00:00Z", "items": [
                    board_item(id="child-board-item", title="Child merge", task="child-anchor", links=[])
                ]}),
                encoding="utf-8",
            )
            (other_home / "data" / "secondmates.md").write_text(
                f"- wren - Keeps the field guide (home: {child_home}; scope: Field guide)\n"
                f"- lark - Keeps the chart (home: {child_board_home}; scope: Chart)\n",
                encoding="utf-8",
            )
            homes = [
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("A", board_home), discover_secondmates=True),
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("B", other_home), discover_secondmates=True),
            ]
            view = quarterdeck.board_view(homes)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual([entry.item.title for entry in view.groups["merge"]], ["Pier merge", "Child merge"])
            self.assertEqual(
                [entry.item.title for entry in view.unsorted],
                ["Unboarded root hold", "Unboarded child hold"],
            )

    def test_merge_keeps_backlog_pr_with_only_lavish_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lavish_url = "https://example.com/lavish/review"
            home = self.make_home(Path(temp), [board_item(
                id="fallback", links=[{"label": "Review page", "url": lavish_url}],
            )])
            homes, view = self.view(home)
            self.assertIsNotNone(view)
            fallback = next(entry for entry in view.groups["merge"] if entry.item.id == "fallback")
            self.assertEqual(fallback.item.links, [("Review page", lavish_url), ("Pull request", PIER_PR)])
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertIn(f'<a href="{lavish_url}">Review page</a>', page)
            self.assertIn(f'<a href="{PIER_PR}">Pull request</a>', page)

    def test_unreadable_backlogs_keep_board_items_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(id="unverified", title="Unverified item")])
            (home / "data" / "backlog.md").unlink()
            homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("root", home), discover_secondmates=True)]
            view = quarterdeck.board_view(homes)
            self.assertIsNotNone(view)
            self.assertEqual([entry.item.title for entry in view.groups["merge"]], ["Unverified item"])
            page = quarterdeck.build_html(homes, "Q")
            self.assertIn('class="warning"', page)
            self.assertIn("Could not check board items for root", page)
            self.assertIn("the backlog could not be read", page)
            self.assertIn("Unverified item", page)

        with tempfile.TemporaryDirectory() as temp:
            child = Path(temp) / "child"
            home = self.make_home(Path(temp), [board_item(
                id="remote", title="Secondmate item", task="wren/remote-task",
            )])
            (child / "data").mkdir(parents=True)
            (home / "data" / "secondmates.md").write_text(
                f"- wren - Keeps the guide (home: {child}; scope: Guide)\n", encoding="utf-8",
            )
            homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("root", home), discover_secondmates=True)]
            view = quarterdeck.board_view(homes)
            self.assertIsNotNone(view)
            self.assertEqual([entry.item.title for entry in view.groups["merge"]], ["Secondmate item"])
            page = quarterdeck.build_html(homes, "Q")
            self.assertIn('class="warning"', page)
            self.assertIn("Could not check remote", page)
            self.assertIn("backlog could not be read", page)
            self.assertIn("the item remains visible", page)

    def test_unsupported_board_shows_warning_and_unsorted_holds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            raw = json.dumps({
                "version": 2,
                "updated_at": "2026-10-08T10:00:00Z",
                "items": [board_item(title="Unsupported board item")],
            })
            homes, view = self.view(self.make_home(Path(temp), raw=raw))
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(view.status, [])
            self.assertEqual(view.groups["merge"], [])
            self.assertIn("kelp-hold", [entry.item.id for entry in view.unsorted])
            self.assertIn("reef-hold", [entry.item.id for entry in view.unsorted])
            page = quarterdeck.build_html(homes, "Q")
            self.assertIn("unsupported version 2", page)
            self.assertNotIn("Unsupported board item", page)
            self.assertIn("Not yet sorted", page)

    def test_malformed_detail_markdown_warns_and_renders_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(detail="Malformed [link](http://[)")])
            homes, _ = self.view(home)
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertIn("detail contains a malformed Markdown URL", page)
            self.assertIn("Malformed [link](http://[)", page)


class BoardWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "invented-home"
        (self.home / "data").mkdir(parents=True)
        self.board = self.home / "data" / "captain-board.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FM_HOME", None)
            os.environ.pop("FM_DATA_OVERRIDE", None)
            code = quarterdeck.main(["board", *args, "--home", str(self.home)])
        return code, stdout.getvalue(), stderr.getvalue()

    def data(self) -> dict[str, object]:
        return json.loads(self.board.read_text(encoding="utf-8"))

    def test_upsert_creates_updates_and_clears_fields(self) -> None:
        code, out, _ = self.cli(
            "upsert", "reef-hold", "--group", "decide", "--title", "Reef buoy", "--ask", "Pick red or green.",
            "--project", "reef-demo", "--brief-url", "https://example.com/brief", "--recommendation", "Choose red",
            "--link", "Brief=https://example.com/brief", "--topic", "Reef",
        )
        self.assertEqual(code, 0)
        self.assertIn("added reef-hold", out)
        item = self.data()["items"][0]
        self.assertEqual(item["brief"], {"url": "https://example.com/brief", "recommendation": "Choose red"})
        self.assertNotIn("touched_at", item)
        self.assertEqual(self.data()["version"], 1)
        self.assertIsNotNone(quarterdeck.parse_board_time(self.data()["updated_at"]))
        code, out, _ = self.cli("upsert", "reef-hold", "--topic", "", "--recommendation", "", "--touch")
        self.assertEqual(code, 0)
        self.assertIn("updated reef-hold", out)
        item = self.data()["items"][0]
        self.assertNotIn("topic", item)
        self.assertEqual(item["brief"], {"url": "https://example.com/brief"})
        self.assertIsNotNone(quarterdeck.parse_board_time(item["touched_at"]))
        board = quarterdeck.parse_captain_board(self.board.read_text(encoding="utf-8"), self.board)
        self.assertEqual(board.warnings, [])
        self.assertEqual(board.items[0].project, "reef-demo")

    def test_upsert_preserves_unknown_keys_and_other_items(self) -> None:
        self.board.write_text(json.dumps({
            "version": 1, "updated_at": "2026-10-01T00:00:00Z", "note": "keep me",
            "items": [board_item(extra="kept"), board_item(id="other", task="other-task")],
        }), encoding="utf-8")
        code, _, _ = self.cli("upsert", "pier-pr", "--ask", "Merge it now.", "--touched-at", "2026-10-08T09:30:00+08:00")
        self.assertEqual(code, 0)
        data = self.data()
        self.assertEqual(data["note"], "keep me")
        self.assertEqual(data["items"][0]["extra"], "kept")
        self.assertEqual(data["items"][0]["ask"], "Merge it now.")
        self.assertEqual(data["items"][0]["touched_at"], "2026-10-08T01:30:00Z")
        self.assertEqual(data["items"][1]["id"], "other")

    def test_upsert_refuses_invalid_input_without_writing(self) -> None:
        cases = [
            ("upsert", "new-task", "--title", "No group", "--ask", "x"),
            ("upsert", "new-task", "--group", "shout", "--title", "t", "--ask", "a"),
            ("upsert", "new-task", "--group", "read", "--title", "t", "--ask", "a", "--brief-url", "javascript:alert(1)"),
            ("upsert", "new-task", "--group", "read", "--title", "t", "--ask", "a", "--link", "nolabel"),
            ("upsert", "new-task", "--group", "read", "--title", "t", "--ask", "a", "--touched-at", "soon"),
        ]
        for args in cases:
            code, _, err = self.cli(*args)
            self.assertEqual(code, 2, args)
            self.assertTrue(err, args)
            self.assertFalse(self.board.exists(), args)

    def test_active_records_messages_and_session_starts(self) -> None:
        self.board.write_text(json.dumps({"version": 1, "updated_at": "2026-10-01T00:00:00Z", "items": [board_item()]}),
                              encoding="utf-8")
        with patch("quarterdeck.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 8, 9, tzinfo=timezone.utc)
            self.assertEqual(self.cli("active")[0], 0)
        self.assertEqual(self.data()["operator"], {
            "last_active_at": "2026-10-08T09:00:00Z", "session_started_at": "2026-10-08T09:00:00Z",
        })
        self.assertEqual(self.data()["items"], [board_item()])
        with patch("quarterdeck.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc)
            self.assertEqual(self.cli("active")[0], 0)
        self.assertEqual(self.data()["operator"]["session_started_at"], "2026-10-08T09:00:00Z")
        self.assertEqual(self.data()["operator"]["last_active_at"], "2026-10-08T12:30:00Z")
        with patch("quarterdeck.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 8, 16, 31, tzinfo=timezone.utc)
            code, out, _ = self.cli("active")
        self.assertEqual(code, 0)
        self.assertIn("session started 2026-10-08T16:31:00Z", out)
        self.assertEqual(self.data()["operator"], {
            "last_active_at": "2026-10-08T16:31:00Z", "session_started_at": "2026-10-08T16:31:00Z",
        })
        with patch("quarterdeck.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 8, 16, 40, tzinfo=timezone.utc)
            self.assertEqual(self.cli("active", "--new-session")[0], 0)
        self.assertEqual(self.data()["operator"]["session_started_at"], "2026-10-08T16:40:00Z")
        board = quarterdeck.parse_captain_board(self.board.read_text(encoding="utf-8"), self.board)
        self.assertEqual(board.warnings, [])
        self.assertEqual(board.last_active_at, datetime(2026, 10, 8, 16, 40, tzinfo=timezone.utc))

    def test_active_records_current_time(self) -> None:
        before = datetime.now(timezone.utc).replace(microsecond=0)
        self.assertEqual(self.cli("active")[0], 0)
        recorded = quarterdeck.parse_board_time(self.data()["operator"]["last_active_at"])
        assert recorded is not None
        self.assertGreaterEqual(recorded, before)
        self.assertEqual(self.data()["items"], [])

    def test_active_rejects_historical_message_replay_without_writing(self) -> None:
        original = json.dumps({
            "version": 1, "items": [board_item(touched_at="2026-10-08T09:30:00Z")],
            "operator": {"last_active_at": "2026-10-08T11:00:00Z",
                         "session_started_at": "2026-10-08T10:00:00Z"},
        })
        self.board.write_text(original, encoding="utf-8")
        for options in ((), ("--new-session",)):
            with self.subTest(options=options), self.assertRaises(SystemExit) as raised:
                self.cli("active", *options, "--at", "2026-10-08T09:00:00Z")
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(self.board.read_text(encoding="utf-8"), original)

    def test_refuses_to_overwrite_unreadable_or_unsupported_board(self) -> None:
        for raw in ("{broken", json.dumps({"version": 2, "items": []}), json.dumps({"version": 1})):
            self.board.write_text(raw, encoding="utf-8")
            for args in (("upsert", "x", "--group", "read", "--title", "t", "--ask", "a"), ("touch", "x"), ("remove", "x"), ("active",)):
                code, _, err = self.cli(*args)
                self.assertEqual(code, 2, (raw, args))
                self.assertIn("quarterdeck:", err)
                self.assertEqual(self.board.read_text(encoding="utf-8"), raw)

    def test_ambiguous_task_needs_id(self) -> None:
        self.board.write_text(json.dumps({"version": 1, "items": [
            board_item(id="one"), board_item(id="two"),
        ]}), encoding="utf-8")
        code, _, err = self.cli("upsert", "pier-pr", "--ask", "x")
        self.assertEqual(code, 2)
        self.assertIn("--id", err)
        code, _, _ = self.cli("upsert", "pier-pr", "--id", "two", "--ask", "Second.")
        self.assertEqual(code, 0)
        self.assertEqual([item["ask"] for item in self.data()["items"]][1], "Second.")

    def test_task_commands_do_not_alias_board_ids(self) -> None:
        original = json.dumps({"version": 1, "items": [board_item(id="dock-fix", task="reef-fix")]})
        for args, expected in ((('touch', 'dock-fix'), 1), (('remove', 'dock-fix'), 0),
                               (('upsert', 'dock-fix', '--ask', 'New ask'), 2)):
            with self.subTest(args=args):
                self.board.write_text(original, encoding="utf-8")
                self.assertEqual(self.cli(*args)[0], expected)
                self.assertEqual(self.board.read_text(encoding="utf-8"), original)
        self.assertEqual(self.cli("upsert", "reef-fix", "--id", "dock-fix", "--ask", "Updated ask")[0], 0)
        self.assertEqual(self.data()["items"][0]["ask"], "Updated ask")
        self.assertEqual(self.cli("remove", "reef-fix", "--id", "dock-fix")[0], 0)
        self.assertEqual(self.data()["items"], [])

    def test_writer_uses_reader_normalized_identity(self) -> None:
        original = json.dumps({"version": 1, "items": [board_item(id=" dock-fix ", task=" reef-fix ")]})
        for task in ("reef-fix", " reef-fix "):
            for command in ("upsert", "touch", "remove"):
                with self.subTest(task=task, command=command):
                    self.board.write_text(original, encoding="utf-8")
                    extra = ("--ask", "Updated ask") if command == "upsert" else ()
                    self.assertEqual(self.cli(command, task, *extra)[0], 0)
                    items = self.data()["items"]
                    if command == "remove":
                        self.assertEqual(items, [])
                    else:
                        self.assertEqual(len(items), 1)
                        parsed = quarterdeck.parse_captain_board(self.board.read_text(encoding="utf-8"), self.board)
                        self.assertEqual((parsed.items[0].id, parsed.items[0].task), ("dock-fix", "reef-fix"))
                        if command == "upsert":
                            self.assertEqual(parsed.items[0].ask, "Updated ask")
                        else:
                            self.assertIsNotNone(parsed.items[0].touched_at)
        for item_id in ("dock-fix", " dock-fix "):
            for command in ("upsert", "remove"):
                with self.subTest(item_id=item_id, command=command):
                    self.board.write_text(original, encoding="utf-8")
                    extra = ("--ask", "Explicit ask") if command == "upsert" else ()
                    self.assertEqual(self.cli(command, "reef-fix", "--id", item_id, *extra)[0], 0)
                    self.assertEqual(len(self.data()["items"]), 1 if command == "upsert" else 0)
        self.board.write_text(original, encoding="utf-8")
        code, _, err = self.cli("upsert", "dock-fix", "--group", "read", "--title", "Dock ask", "--ask", "Read it")
        self.assertEqual(code, 2)
        self.assertIn("already names another task", err)
        self.assertEqual(self.board.read_text(encoding="utf-8"), original)

    def test_upsert_validates_the_changed_record_and_duplicate_identity(self) -> None:
        for item in (board_item(id="dock-fix", task="reef-fix"),
                     {"id": "dock-fix", "task": "reef-fix", "group": "read", "ask": "Read it"}):
            with self.subTest(item=item):
                original = json.dumps({"version": 1, "items": [board_item(id=" dock-fix ", task="other-task"), item]})
                self.board.write_text(original, encoding="utf-8")
                self.assertEqual(self.cli("upsert", "reef-fix", "--ask", "Updated ask")[0], 2)
                self.assertEqual(self.board.read_text(encoding="utf-8"), original)
        original = json.dumps({"version": 1, "items": [{"id": "dock-fix", "task": "reef-fix", "group": "read", "ask": "Read it"}]})
        self.board.write_text(original, encoding="utf-8")
        self.assertEqual(self.cli("upsert", "reef-fix", "--ask", "Updated ask")[0], 2)
        self.assertEqual(self.board.read_text(encoding="utf-8"), original)

    def test_matched_items_without_ids_are_rejected_before_writing(self) -> None:
        for invalid_id in (None, "", "   ", 7):
            for command in ("touch", "upsert", "remove"):
                with self.subTest(invalid_id=invalid_id, command=command):
                    item = {"task": "dock-fix"}
                    if invalid_id is not None:
                        item["id"] = invalid_id
                    original = json.dumps({"version": 1, "items": [board_item(task="dock-fix"), item]})
                    self.board.write_text(original, encoding="utf-8")
                    code, _, err = self.cli(command, "dock-fix")
                    self.assertEqual(code, 2)
                    self.assertIn("invalid id", err)
                    self.assertEqual(self.board.read_text(encoding="utf-8"), original)

    def test_touch_keeps_the_newer_time_and_reports_missing(self) -> None:
        self.board.write_text(json.dumps({"version": 1, "items": [
            board_item(touched_at="2026-10-08T00:00:00Z"), board_item(id="other", task="other-task"),
        ]}), encoding="utf-8")
        self.assertEqual(self.cli("touch", "pier-pr", "--at", "2026-10-01T00:00:00Z")[0], 0)
        self.assertEqual(self.data()["items"][0]["touched_at"], "2026-10-08T00:00:00Z")
        self.assertEqual(self.cli("touch", "pier-pr", "--at", "2026-10-09T00:00:00Z")[0], 0)
        self.assertEqual(self.data()["items"][0]["touched_at"], "2026-10-09T00:00:00Z")
        self.assertNotIn("touched_at", self.data()["items"][1])
        code, _, err = self.cli("touch", "no-such-task")
        self.assertEqual(code, 1)
        self.assertIn("board upsert", err)

    def test_remove_is_idempotent_and_writes_only_the_board(self) -> None:
        self.assertEqual(self.cli("remove", "pier-pr")[0], 0)
        self.assertFalse(self.board.exists())
        self.board.write_text(json.dumps({"version": 1, "items": [board_item(), board_item(id="other", task="other-task")]}), encoding="utf-8")
        code, out, _ = self.cli("remove", "pier-pr")
        self.assertEqual(code, 0)
        self.assertIn("removed pier-merge", out)
        self.assertEqual([item["id"] for item in self.data()["items"]], ["other"])
        self.assertEqual(self.cli("remove", "pier-pr")[0], 0)
        self.assertEqual(sorted(path.name for path in (self.home / "data").iterdir()), ["captain-board.json"])

    def test_list_prints_items_and_json(self) -> None:
        self.cli("upsert", "reef-hold", "--group", "decide", "--title", "Reef buoy", "--ask", "Pick.", "--touched-at", "2026-10-08T00:00:00Z")
        code, out, _ = self.cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "reef-hold\treef-hold\tdecide\t2026-10-08T00:00:00Z\tReef buoy")
        code, out, _ = self.cli("list", "--json")
        self.assertEqual(json.loads(out)["items"][0]["id"], "reef-hold")

    def test_refuses_symlinked_board_and_missing_home(self) -> None:
        target = Path(self.temp.name) / "elsewhere.json"
        target.write_text(json.dumps({"version": 1, "items": []}), encoding="utf-8")
        self.board.symlink_to(target)
        code, _, err = self.cli("touch", "x")
        self.assertEqual(code, 2)
        self.assertIn("symbolic-link", err)
        stderr = io.StringIO()
        with redirect_stderr(stderr), patch.dict(os.environ, {"FM_HOME": ""}):
            self.assertEqual(quarterdeck.main(["board", "list"]), 2)
        self.assertIn("Firstmate home is required", stderr.getvalue())


class BearingsTests(unittest.TestCase):
    def make_home(self, root: Path, snapshot_script: str | None = None) -> Path:
        home = root / "invented-home"
        (home / "data" / "kelp-survey").mkdir(parents=True)
        (home / "data" / "backlog.md").write_text(BULLET_BACKLOG, encoding="utf-8")
        (home / "data" / "kelp-survey" / "report.md").write_text(
            "# Kelp survey report\n\n## Recommendation\n\nWait for spring.\n", encoding="utf-8")
        if snapshot_script is not None:
            script = home / "bin" / "fm-bearings-snapshot.sh"
            script.parent.mkdir()
            script.write_text(snapshot_script, encoding="utf-8")
            script.chmod(0o755)
        return home

    def snapshots(self, home: Path) -> list:
        homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("invented", home), discover_secondmates=True)]
        quarterdeck.apply_review_marks(homes, {})
        return homes

    def assert_four_sections(self, page: str) -> None:
        order = [page.index(f'id="{key}"') for key in ("call", "landed", "underway", "charted")]
        self.assertEqual(order, sorted(order))
        for title in ("Captain&#x27;s Call", "Recently Landed", "Underway", "Charted Next",
                      "Reports waiting on your review"):
            self.assertIn(title, page)

    def test_snapshot_path_classifies_fields_and_enriches_from_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), "#!/bin/sh\ncat <<'JSON'\n" + json.dumps(SNAPSHOT_JSON) + "\nJSON\n")
            now = datetime(2026, 10, 4, tzinfo=timezone.utc)
            bearings = quarterdeck.build_bearings(self.snapshots(home), now=now)
            self.assertEqual(bearings.sources[0].mode, "snapshot")
            self.assertEqual([i.item_id for i in bearings.call], ["kelp-survey"])
            self.assertEqual([i.item_id for i in bearings.landed], ["pier-fix"])
            self.assertEqual([i.title for i in bearings.underway], ["Harbor chart"])
            self.assertEqual([i.item_id for i in bearings.charted], ["later-work"])
            page = quarterdeck.bearings_html(bearings)
            self.assert_four_sections(page)
            # The full backlog text replaces the snapshot's truncated summary.
            self.assertIn("Waiting on the invented diver roster", page)
            self.assertIn("Second paragraph with the long body text", page)
            self.assertNotIn("truncated sum", page)
            self.assertIn("(a) survey now, (b) wait for spring.", page)
            self.assertIn("wait for spring (cheaper).", page)
            self.assertIn("waiting 14 days", page)
            self.assertIn("project reef-demo", page)
            self.assertIn("Read report", page)
            self.assertIn(f'href="{PIER_PR}">{PIER_PR}<', page)
            self.assertIn("harbor-chart — queued behind the chart", page)
            self.assertIn("Firstmate bearings snapshot", page)

    def test_snapshot_secondmate_work_uses_registered_home_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            child_home = root / "kestrel-home"
            (child_home / "data").mkdir(parents=True)
            (child_home / "data" / "backlog.md").write_text("# Secondmate backlog\n", encoding="utf-8")
            (home / "data" / "secondmates.md").write_text(
                f"- kestrel - Maintains the field guide (home: {child_home}; scope: Field guide)\n",
                encoding="utf-8",
            )
            snapshots = self.snapshots(home)
            child = next(child for child in snapshots[0].children if child.spec.route_id == "kestrel")
            payload = {
                **SNAPSHOT_JSON,
                "decisions_open": [{"owner": "kestrel", "key": "remote-decision",
                                    "summary": "Approve the field guide", "verb": "review"}],
                "landed": [{"owner": "kestrel", "id": "remote-landed", "what": "Field guide update",
                            "artifact": "-"}],
                "in_flight": [{"id": "kestrel/kestrel-work", "name": "Willow Quay field guide",
                               "repo": None, "state": "working", "doing": "mapping"}],
                "gates": [{"owner": "kestrel", "id": "remote-gate", "title": "Wait for the field guide",
                           "filed": None, "blocked_by": "field guide", "reason": "review pending"}],
            }
            calls = []
            bearings = quarterdeck.build_bearings(
                snapshots, runner=lambda selected_home, _data_dir: (calls.append(selected_home) or payload, ""))
            task = next(item for item in bearings.underway if item.item_id == "kestrel/kestrel-work")
            self.assertEqual(task.title, "Willow Quay field guide")
            self.assertEqual(task.owner, child.spec.label)
            self.assertEqual(bearings.call[0].owner, child.spec.label)
            self.assertEqual(bearings.call[0].title, "Approve the field guide")
            self.assertEqual(bearings.call[0].detail,
                             f"Full background lives in the secondmate home: {child.spec.label}.")
            self.assertEqual(bearings.landed[0].owner, child.spec.label)
            self.assertEqual(bearings.charted[-1].owner, child.spec.label)
            self.assertEqual(calls, [home])

    def test_snapshot_call_from_unregistered_secondmate_keeps_its_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            snapshots = self.snapshots(home)
            parent_label = snapshots[0].spec.label
            payload = {**SNAPSHOT_JSON, "decisions_open": [
                {"owner": "wren", "key": "task", "summary": "Unregistered call", "verb": "decide"},
            ]}
            bearings = quarterdeck.build_bearings(snapshots, runner=lambda *_args: (payload, ""))
            self.assertEqual([(item.owner, item.item_id) for item in bearings.call], [("wren", "task")])
            quarterdeck.apply_review_marks(snapshots, quarterdeck.ReviewState(
                relevance={("call", quarterdeck.report_reference(parent_label, "task")): ("stale", "parent call")},
            ))
            bearings = quarterdeck.build_bearings(snapshots, runner=lambda *_args: (payload, ""))
            self.assertEqual([item.title for item in bearings.call], ["Unregistered call"])

    def test_secondmate_summaries_do_not_duplicate_live_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            payload = {
                **SNAPSHOT_JSON,
                "decisions_open": [*SNAPSHOT_JSON["decisions_open"],
                                    {"owner": "decision-route", "key": "decision-route/task",
                                     "summary": "Resolve the route decision", "verb": "review"}],
                "in_flight": [*SNAPSHOT_JSON["in_flight"],
                              {"id": "kestrel/kestrel-task", "name": "Live child task", "repo": None,
                               "state": "working", "doing": "moving buoys"}],
                "secondmates": [
                    {"id": "kestrel", "state": "active_child_work", "doing": "moving buoys", "reason": ""},
                    {"id": "held-route", "state": "externally_held", "doing": "waiting for access",
                     "reason": "owner approval"},
                    {"id": "unknown-route", "state": "unrecognized", "doing": "status unavailable",
                     "reason": ""},
                    {"id": "idle-route", "state": "no_active_work", "doing": "", "reason": ""},
                    {"id": "decision-route", "state": "captain_decision", "doing": "one decision",
                     "reason": ""},
                ],
            }
            bearings = quarterdeck.build_bearings(
                self.snapshots(home), runner=lambda _home, _data_dir: (payload, ""))
            self.assertEqual([item.item_id for item in bearings.underway], ["harbor-chart", "kestrel/kestrel-task"])
            self.assertCountEqual([item.item_id for item in bearings.charted],
                                  ["later-work", "held-route", "unknown-route"])
            self.assertEqual([item.item_id for item in bearings.call], ["kelp-survey", "decision-route/task"])
            all_ids = [item.item_id for items in (bearings.call, bearings.landed, bearings.underway, bearings.charted)
                       for item in items]
            self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_snapshot_omissions_are_visible_in_the_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            payload = {
                **SNAPSHOT_JSON,
                "omitted": [{"surface": "landed rows", "reveal": "run bearings with <all>"}],
            }
            bearings = quarterdeck.build_bearings(
                self.snapshots(home), runner=lambda _home, _data_dir: (payload, ""))
            page = quarterdeck.bearings_html(bearings)
            self.assertIn("Snapshot omissions", page)
            self.assertIn("invented", page)
            self.assertIn("landed rows", page)
            self.assertIn("run bearings with &lt;all&gt;", page)

    def test_snapshot_script_receives_fm_home_and_json_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            script = ('#!/bin/sh\nprintf \'{"schema":"fm-bearings.v1","home":"%s","args":"%s",'
                      '"decisions_open":[],"landed":[],"in_flight":[],"secondmates":[],"gates":[]}\' "$FM_HOME" "$*"\n')
            home = self.make_home(Path(temp), script)
            data, reason = quarterdeck.run_bearings_snapshot(home)
            self.assertEqual(reason, "")
            self.assertEqual(data["home"], str(home))
            self.assertEqual(data["args"], "--json")

    def test_fallback_path_classifies_backlog_and_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            now = datetime(2026, 10, 4, tzinfo=timezone.utc)
            bearings = quarterdeck.build_bearings(self.snapshots(home), now=now)
            self.assertEqual(bearings.sources[0].mode, "fallback")
            self.assertEqual([i.item_id for i in bearings.call], ["kelp-survey"])
            self.assertEqual([i.item_id for i in bearings.landed], ["pier-fix", "old-dock"])
            self.assertEqual([i.item_id for i in bearings.underway], ["harbor-chart"])
            self.assertEqual([i.item_id for i in bearings.charted], ["later-work"])
            self.assertEqual(bearings.charted[0].waits_on, "queue order")
            page = quarterdeck.bearings_html(bearings)
            self.assert_four_sections(page)
            self.assertIn("fallback", page)
            self.assertIn("no executable bin/fm-bearings-snapshot.sh", page)
            self.assertIn("waiting on the invented diver roster".lower(), page.lower())
            self.assertIn("Second paragraph with the long body text", page)
            self.assertIn("(a) survey now, (b) wait for spring.", page)
            self.assertIn(PIER_PR, page)
            self.assertIn("waiting 14 days", page)
            self.assertIn("Kelp survey report", page)  # report is listed as needing review

    def test_fallback_when_script_fails_or_times_out_or_prints_garbage(self) -> None:
        cases = {
            "exit": ("#!/bin/sh\nexit 3\n", "status 3"),
            "garbage": ("#!/bin/sh\necho not json\n", "did not print JSON"),
            "schema": ('#!/bin/sh\necho \'{"schema":"other"}\'\n', "unrecognized schema"),
            "slow": ("#!/bin/sh\nsleep 5\n", "timed out"),
            "invalid utf-8": ("#!/bin/sh\nprintf '\\377'\n", "invalid UTF-8"),
        }
        for name, (script, expected) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as temp:
                home = self.make_home(Path(temp), script)
                bearings = quarterdeck.build_bearings(
                    self.snapshots(home), runner=lambda h, data_dir: quarterdeck.run_bearings_snapshot(
                        h, data_dir=data_dir, timeout=0.5))
                self.assertEqual(bearings.sources[0].mode, "fallback")
                self.assertIn(expected, bearings.sources[0].reason)
                self.assertEqual([i.item_id for i in bearings.call], ["kelp-survey"])

    def test_snapshot_allows_nullable_fields_and_uses_selected_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            data_dir = root / "archive" / "data"
            data_dir.mkdir(parents=True)
            (data_dir / "backlog.md").write_text(
                (home / "data" / "backlog.md").read_text(encoding="utf-8"), encoding="utf-8")
            payload = {
                "schema": "fm-bearings.v1",
                "decisions_open": [],
                "landed": [],
                "in_flight": [{"id": "archive-task", "name": "", "repo": None,
                               "state": "working", "doing": ""}],
                "secondmates": [],
                "gates": [{"owner": "(main)", "id": "archive-gate", "title": "Archive gate",
                           "filed": None, "blocked_by": "", "reason": ""}],
            }
            payload_script = (
                f"#!{sys.executable}\n"
                "import json, os\n"
                f"payload = {payload!r}\n"
                "payload['in_flight'][0]['name'] = os.environ['FM_DATA_OVERRIDE']\n"
                "print(json.dumps(payload))\n"
            )
            script = home / "bin" / "fm-bearings-snapshot.sh"
            script.parent.mkdir()
            script.write_text(payload_script, encoding="utf-8")
            script.chmod(0o755)
            snapshot = quarterdeck.load_home_snapshot(
                quarterdeck.HomeSpec("archive-home", home, data_dir=data_dir))
            with patch.dict(os.environ, {"FM_DATA_OVERRIDE": str(root / "wrong-data")}):
                bearings = quarterdeck.build_bearings([snapshot])
            self.assertEqual(bearings.sources[0].mode, "snapshot")
            self.assertEqual(bearings.underway[0].title, str(data_dir.resolve()))
            self.assertEqual(bearings.charted[0].filed, "")

    def test_malformed_snapshot_shapes_fall_back_to_the_backlog(self) -> None:
        malformed = [
            ({**SNAPSHOT_JSON, "schema": "fm-bearings.future"}, "unrecognized schema"),
            ({key: value for key, value in SNAPSHOT_JSON.items() if key != "decisions_open"}, "decisions_open is not an array"),
            ({**SNAPSHOT_JSON, "in_flight": [{**SNAPSHOT_JSON["in_flight"][0], "repo": 3}]}, "invalid repo"),
            ({**SNAPSHOT_JSON, "omitted": [{"surface": 3, "reveal": "run with --all"}]}, "invalid details"),
        ]
        for payload, expected in malformed:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                script = "#!/bin/sh\nprintf '%s' '" + json.dumps(payload) + "'\n"
                home = self.make_home(Path(temp), script)
                bearings = quarterdeck.build_bearings(self.snapshots(home))
                self.assertEqual(bearings.sources[0].mode, "fallback")
                self.assertIn(expected, bearings.sources[0].reason)
                self.assertEqual([item.item_id for item in bearings.call], ["kelp-survey"])

    def test_bullet_fallback_uses_closed_review_and_live_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            backlog = home / "data" / "backlog.md"
            extra = """

## More work
- [ ] review-patch - Review the added patch (repo: patch-demo) (state: review_ready) (pr: https://github.com/example/quarterdeck-demo/pull/84)
- [ ] running-task - Resume the running survey (state: running)
- [ ] progress-task - Continue the active chart (status: in_progress)

## Finished
- [ ] finished-task - File the completed sample
"""
            backlog.write_text(backlog.read_text(encoding="utf-8") + extra, encoding="utf-8")
            bearings = quarterdeck.build_bearings(self.snapshots(home))
            self.assertIn("review-patch", [item.item_id for item in bearings.call])
            self.assertTrue({"running-task", "progress-task"}.issubset({item.item_id for item in bearings.underway}))
            self.assertIn("finished-task", [item.item_id for item in bearings.landed])

    def test_bullet_fallback_requires_an_affirmative_hold_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            backlog = home / "data" / "backlog.md"
            backlog.write_text(backlog.read_text(encoding="utf-8") + """

## Hold markers
- [ ] released-hold - A released sample (held: no) (hold: stale reason) (hold-kind: captain)
- [ ] missing-hold - A queued sample (hold: -) (hold-kind: captain)
- [ ] current-hold - A current sample (held: yes) (hold: choose a route) (hold-kind: captain)
""", encoding="utf-8")
            bearings = quarterdeck.build_bearings(self.snapshots(home))
            self.assertEqual(
                {item.item_id for item in bearings.call},
                {"kelp-survey", "current-hold"},
            )
            self.assertTrue({"released-hold", "missing-hold"}.issubset(
                {item.item_id for item in bearings.charted}
            ))

    def test_merged_state_and_date_are_done_in_bullet_and_table_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            backlog = home / "data" / "backlog.md"
            backlog.write_text(backlog.read_text(encoding="utf-8") + """

## Merged bullets
- [ ] merged-state-bullet - Merged from state (state: merged)
- [ ] merged-date-bullet - Merged by date (state: working) (merged: 2026-10-02)
- [ ] merged-none-bullet - Not merged (state: working) (merged: (none))

## Merged table
| ID | Task | State | Merged |
| --- | --- | --- | --- |
| merged-state-table | Merged table state | merged | - |
| merged-date-table | Merged table date | working | 2026-10-03 |
| merged-none-table | Not merged table | working | (none) |
""", encoding="utf-8")
            snapshot = self.snapshots(home)[0]
            bearings = quarterdeck.build_bearings([snapshot], use_snapshot=False)
            landed = {item.item_id: item for item in bearings.landed}
            for item_id in (
                "merged-state-bullet", "merged-date-bullet", "merged-state-table", "merged-date-table",
            ):
                self.assertIn(item_id, landed)
                self.assertNotIn(item_id, {item.item_id for item in bearings.charted})
            for item_id in ("merged-none-bullet", "merged-none-table"):
                self.assertNotIn(item_id, landed)
                self.assertIn(item_id, {item.item_id for item in bearings.underway})
            self.assertEqual(landed["merged-date-bullet"].filed, "2026-10-02")
            self.assertEqual(landed["merged-date-table"].filed, "2026-10-03")

    def test_cancelled_tasks_are_omitted_from_all_bearings_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            backlog = home / "data" / "backlog.md"
            backlog.write_text(backlog.read_text(encoding="utf-8") + f"""

## Cancelled tasks
- [ ] cancelled-review - Cancelled review task (state: cancelled) (held: yes) (hold-kind: captain) (review_ready: yes) (pr: {PIER_PR})
- [x] canceled-checked - Canceled checked task (state: canceled) (merged: 2026-10-02)
- [ ] cancelled-live - Cancelled active task (state: cancelled)
- [ ] canceled-gate - Canceled queued task (state: canceled)

## Cancelled table
| ID | Task | State | Closed | Blocked by |
| --- | --- | --- | --- | --- |
| canceled-table | Canceled table task | canceled | 2026-10-03 | captain |
""", encoding="utf-8")
            snapshot = self.snapshots(home)[0]
            cancelled_ids = {"cancelled-review", "canceled-checked", "cancelled-live", "canceled-gate", "canceled-table"}
            groups = quarterdeck.attention_groups(snapshot, show_all=True)
            grouped_ids = {
                quarterdeck.value_for(record.fields, "id")
                for records in groups.values() for record in records
            }
            self.assertTrue(cancelled_ids.isdisjoint(grouped_ids))

            fallback = quarterdeck.build_bearings([snapshot], use_snapshot=False)
            for section in (fallback.call, fallback.landed, fallback.underway, fallback.charted):
                self.assertTrue(cancelled_ids.isdisjoint(item.item_id for item in section))

            payload = {
                **SNAPSHOT_JSON,
                "decisions_open": [{"owner": "(main)", "key": "cancelled-review", "id": "cancelled-review",
                                    "summary": "Cancelled review task", "verb": "captain-hold"}],
                "landed": [{"owner": "(main)", "id": "canceled-table", "what": "Canceled table task",
                            "artifact": "-"}],
                "in_flight": [{"id": "cancelled-live", "name": "Cancelled active task", "repo": None,
                               "state": "working", "doing": "-"}],
                "gates": [{"owner": "(main)", "id": "canceled-gate", "title": "Canceled queued task",
                           "filed": None, "blocked_by": "-", "reason": "-"}],
            }
            snapshot_result = quarterdeck.build_bearings(
                [snapshot], runner=lambda _home, _data_dir: (payload, ""))
            for section in (snapshot_result.call, snapshot_result.landed, snapshot_result.underway,
                            snapshot_result.charted):
                self.assertTrue(cancelled_ids.isdisjoint(item.item_id for item in section))

    def test_no_snapshot_option_skips_the_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "ran"
            home = self.make_home(Path(temp), f"#!/bin/sh\ntouch {marker}\n")
            bearings = quarterdeck.build_bearings(self.snapshots(home), use_snapshot=False)
            self.assertFalse(marker.exists())
            self.assertIn("--no-snapshot", bearings.sources[0].reason)

    def test_every_section_renders_an_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "empty-home"
            (home / "data").mkdir(parents=True)
            (home / "data" / "backlog.md").write_text("# Backlog\n", encoding="utf-8")
            page = quarterdeck.bearings_html(quarterdeck.build_bearings(self.snapshots(home)))
            self.assertEqual(page.count('class="empty"'), 5)
            self.assertIn("Nothing is waiting on a captain decision.", page)
            self.assertIn("No reports are waiting on your review.", page)


class ServeTests(unittest.TestCase):
    def make_args(self, home: Path, **extra: object) -> object:
        import argparse
        values = dict(home=str(home), config=None, title=None, no_snapshot=True)
        values.update(extra)
        return argparse.Namespace(**values)

    def start(self, cache: quarterdeck.SiteCache):
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), quarterdeck.make_handler(cache))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server.server_address[1]

    def fetch(self, port: int, path: str, method: str = "GET", headers: dict[str, str] | None = None):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        return response.status, response.headers, body

    def test_cache_reuses_render_until_ttl_then_rerenders(self) -> None:
        calls: list[int] = []
        clock = [100.0]
        cache = quarterdeck.SiteCache(lambda: calls.append(1) or {"index.html": str(len(calls))}, 30, lambda: clock[0])
        self.assertEqual(cache.get()["index.html"], "1")
        clock[0] += 29
        self.assertEqual(cache.get()["index.html"], "1")
        clock[0] += 1
        self.assertEqual(cache.get()["index.html"], "2")
        self.assertEqual(len(calls), 2)

    def test_server_renders_per_request_and_serves_linked_pages_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            home = BearingsTests().make_home(Path(temp))
            clock = [0.0]
            cache = quarterdeck.SiteCache(
                quarterdeck.site_builder(self.make_args(home)), 30, lambda: clock[0])
            port = self.start(cache)
            status, headers, body = self.fetch(port, "/")
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertIn("Kelp survey: map the invented reef", body)
            self.assertIn("Generated at", body)
            self.assertIn("}, 60000);", body)
            links = re.findall(r'href="((?:item|report|backlog)-[0-9a-f]{12}\.html)"', body)
            self.assertTrue(any(link.startswith("item-") for link in links))
            self.assertTrue(any(link.startswith("report-") for link in links))
            for link in set(links):
                self.assertEqual(self.fetch(port, "/" + link)[0], 200, link)
            # Edits show up only after the cache expires.
            backlog = home / "data" / "backlog.md"
            backlog.write_text(backlog.read_text(encoding="utf-8").replace("Later work: sort", "Renamed work: sort"),
                               encoding="utf-8")
            self.assertIn("Later work: sort", self.fetch(port, "/")[2])
            clock[0] += 31
            fresh = self.fetch(port, "/index.html?x=1")[2]
            self.assertIn("Renamed work: sort", fresh)
            self.assertNotIn("Later work: sort", fresh)
            # Read only: nothing but GET, and only generated pages.
            for method in ("POST", "PUT", "DELETE", "HEAD"):
                self.assertEqual(self.fetch(port, "/", method)[0], 405, method)
            self.assertEqual(self.fetch(port, "/../data/backlog.md")[0], 404)
            self.assertEqual(self.fetch(port, "/item-000000000000.html")[0], 404)

    def test_serve_listens_on_every_requested_address_on_one_port(self) -> None:
        import socket
        import urllib.request
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "s")}):
            home = BearingsTests().make_home(Path(temp))
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            args = quarterdeck.make_parser().parse_args(
                ["serve", "--home", str(home), "--no-snapshot", "--port", str(port),
                 "--host", "127.0.0.1", "--host", "127.0.0.2"])
            self.assertEqual(quarterdeck.bind_hosts(args), ["127.0.0.1", "127.0.0.2"])
            thread = threading.Thread(target=quarterdeck.serve, args=(args,), daemon=True)
            with redirect_stdout(io.StringIO()):
                thread.start()
                for host in ("127.0.0.1", "127.0.0.2"):
                    for _ in range(100):
                        try:
                            body = urllib.request.urlopen(f"http://{host}:{port}/", timeout=2).read().decode()
                            break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        self.fail(f"not listening on {host}")
                    self.assertIn("Captain&#x27;s Call", body)

    def test_ipv6_wildcard_and_ipv4_wildcard_can_share_a_port(self) -> None:
        import socket
        handler = quarterdeck.make_handler(quarterdeck.SiteCache(lambda: {"index.html": "ok"}))
        try:
            ipv6 = quarterdeck.make_server("::", 0, handler)
        except OSError as exc:
            self.skipTest(f"IPv6 wildcard binding is unavailable: {exc}")
        self.addCleanup(ipv6.server_close)
        try:
            ipv4 = quarterdeck.make_server("0.0.0.0", ipv6.server_address[1], handler)
        except OSError as exc:
            self.fail(f"IPv4 wildcard could not share the IPv6 listener port: {exc}")
        self.addCleanup(ipv4.server_close)
        self.assertEqual(ipv6.address_family, socket.AF_INET6)
        self.assertEqual(ipv4.address_family, socket.AF_INET)

    def test_ipv4_wildcard_classification_does_not_require_ipv6_mapping(self) -> None:
        self.assertFalse(quarterdeck.is_loopback("0.0.0.0"))
        self.assertFalse(quarterdeck.request_host_is_loopback("0.0.0.0"))

    def start_marking(self, temp: str, **extra: object):
        home = BearingsTests().make_home(Path(temp))
        args = self.make_args(home, allow_marks=True, **extra)
        cache = quarterdeck.SiteCache(quarterdeck.site_builder(args, True), 30)
        handler = quarterdeck.make_handler(
            cache, lambda report_id, action, fingerprint: quarterdeck.set_report_action(
                args, report_id, action, fingerprint
            ))
        return self.start_handler(handler), cache, home

    def start_handler(self, handler):
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server.server_address[1]

    def post_mark(self, port: int, payload: object, headers: dict[str, str] | None = None,
                  path: str = quarterdeck.MARK_ENDPOINT):
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        body = payload if isinstance(payload, str) else json.dumps(payload)
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("POST", path, body=body, headers=request_headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8")
        connection.close()
        return response.status, text

    def test_marks_endpoint_marks_and_unmarks_through_the_cli_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            port, _cache, _home = self.start_marking(temp)
            status, page = self.fetch(port, "/")[0::2]
            self.assertEqual(status, 200)
            self.assertNotIn("X-Quarterdeck-Token", page)
            self.assertNotIn("mark-token", page)
            self.assertIn("Mark reviewed", page)
            self.assertNotIn("after reading: quarterdeck reports mark-reviewed", page)
            button = re.search(
                r'data-mark-report="([^"]+)" data-mark-fingerprint="([0-9a-f]{64})" data-mark-action="mark"', page
            )
            report_id, fingerprint = button.groups()
            status, body = self.post_mark(port, {
                "report_id": report_id, "fingerprint": fingerprint, "action": "mark"
            },
                                          {"Origin": f"http://127.0.0.1:{port}", "Host": f"127.0.0.1:{port}"})
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body), {"report_id": report_id, "reviewed": True, "state": "reviewed"})
            marks = quarterdeck.load_review_marks(quarterdeck.review_state_path())
            self.assertIn(report_id, marks)
            page = self.fetch(port, "/")[2]  # the cache was invalidated, so no manual re-render is needed
            self.assertNotIn(
                f'data-mark-report="{report_id}" data-mark-fingerprint="{fingerprint}" data-mark-action="mark"',
                page,
            )
            self.assertIn(f'data-mark-report="{report_id}" data-mark-fingerprint="{fingerprint}" data-mark-action="unmark"', page)
            status, body = self.post_mark(port, {
                "report_id": report_id, "fingerprint": fingerprint, "action": "unmark"
            })
            self.assertEqual(status, 200, body)
            self.assertNotIn(report_id, quarterdeck.load_review_marks(quarterdeck.review_state_path()))
            self.assertIn(f'data-mark-report="{report_id}" data-mark-fingerprint="{fingerprint}" data-mark-action="mark"', self.fetch(port, "/")[2])
            self.assertFalse(quarterdeck.review_state_path().with_name("mark-token").exists())

    def test_marks_endpoint_refuses_bad_requests_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            port, _cache, _home = self.start_marking(temp)
            page = self.fetch(port, "/")[2]
            button = re.search(
                r'data-mark-report="([^"]+)" data-mark-fingerprint="([0-9a-f]{64})"', page
            )
            report_id, fingerprint = button.groups()
            good = {"report_id": report_id, "fingerprint": fingerprint, "action": "mark"}
            self.assertEqual(self.post_mark(port, good, {"Origin": "http://attacker.example"})[0], 403)
            self.assertEqual(self.post_mark(port, good, {"Sec-Fetch-Site": "cross-site"})[0], 403)
            self.assertEqual(self.post_mark(port, good, {"Host": "attacker.example"})[0], 403)
            self.assertEqual(self.post_mark(port, good, {"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(self.post_mark(port, {
                "report_id": "nope/unknown-1", "fingerprint": fingerprint, "action": "mark"
            })[0], 404)
            self.assertEqual(self.post_mark(port, {**good, "action": "delete"})[0], 400)
            self.assertEqual(self.post_mark(port, {**good, "action": []})[0], 400)
            self.assertEqual(self.post_mark(port, {**good, "extra": 1})[0], 400)
            self.assertEqual(self.post_mark(port, "not json")[0], 400)
            self.assertEqual(self.post_mark(port, good, path="/api/other")[0], 405)
            self.assertEqual(self.post_mark(port, good, path="/")[0], 405)
            for method in ("PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                self.assertEqual(self.fetch(port, quarterdeck.MARK_ENDPOINT, method)[0], 405, method)
            self.assertEqual(self.fetch(port, quarterdeck.MARK_ENDPOINT)[0], 404)
            self.assertEqual(quarterdeck.load_review_marks(quarterdeck.review_state_path()), {})

    def test_marks_endpoint_rejects_a_stale_page_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            port, _cache, home = self.start_marking(temp)
            page = self.fetch(port, "/")[2]
            button = re.search(
                r'data-mark-report="([^"]+)" data-mark-fingerprint="([0-9a-f]{64})" data-mark-action="mark"', page
            )
            report_id, fingerprint = button.groups()
            report_path = home / "data" / report_id.split("/", 1)[1] / "report.md"
            report_path.write_text(report_path.read_text(encoding="utf-8") + "\nChanged after the page loaded.\n", encoding="utf-8")
            status, body = self.post_mark(port, {
                "report_id": report_id, "fingerprint": fingerprint, "action": "mark"
            })
            self.assertEqual(status, 409, body)
            self.assertIn("report changed since the page was loaded", body)
            self.assertEqual(quarterdeck.load_review_marks(quarterdeck.review_state_path()), {})

    def test_marks_are_refused_and_hidden_unless_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            home = BearingsTests().make_home(Path(temp))
            args = self.make_args(home)
            port = self.start(quarterdeck.SiteCache(quarterdeck.site_builder(args), 30))
            page = self.fetch(port, "/")[2]
            self.assertNotIn("data-mark-report", page)
            self.assertNotIn("mark-token", page)
            self.assertIn("after reading: quarterdeck reports mark-reviewed", page)
            report_id = re.search(r"quarterdeck reports mark-reviewed ([^<]+)<", page).group(1)
            self.assertEqual(self.post_mark(port, {"report_id": report_id, "fingerprint": "0" * 64, "action": "mark"})[0], 405)
            self.assertFalse(quarterdeck.review_state_path().exists())
            self.assertFalse(quarterdeck.review_state_path().with_name("mark-token").exists())
            # Static render never carries the buttons.
            snapshots = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("demo", home), discover_secondmates=True)]
            quarterdeck.apply_review_marks(snapshots, {})
            self.assertNotIn("data-mark-report", quarterdeck.build_html(snapshots, "T"))

    def test_allow_marks_option_parses_and_reaches_service_unit(self) -> None:
        parser = quarterdeck.make_parser()
        self.assertFalse(parser.parse_args(["serve"]).allow_marks)
        args = parser.parse_args(["service", "--allow-marks"])
        self.assertTrue(args.allow_marks)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": tempfile.gettempdir()}, clear=True):
            command = parse_systemd_unit(quarterdeck.service_unit(args))["Service"]["ExecStart"][0]
            self.assertIn("--allow-marks", command)

    def test_allow_marks_preflight_accepts_board_when_backlog_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}
        ):
            home = CaptainBoardTests().make_home(
                Path(temp), [board_item(id="unverified", title="Unverified board item")]
            )
            (home / "data" / "backlog.md").unlink()
            args = quarterdeck.make_parser().parse_args(
                ["serve", "--home", str(home), "--no-snapshot", "--allow-marks"]
            )

            snapshots = quarterdeck.load_report_snapshots(args)[0]
            self.assertIsNotNone(snapshots[0].error)
            self.assertIsNotNone(snapshots[0].board)
            page = quarterdeck.site_builder(args, True)()["index.html"]

            self.assertIn("Unverified board item", page)
            self.assertIn("Could not check board items", page)

    def test_ambiguous_report_ids_render_disabled_mark_buttons(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = BearingsTests().make_home(root / "first")
            second = BearingsTests().make_home(root / "second")
            homes = [
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Shared Home", first), discover_secondmates=True),
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Shared Home", second), discover_secondmates=True),
            ]
            quarterdeck.apply_review_marks(homes, {})
            bearings = quarterdeck.build_bearings(homes, use_snapshot=False)
            page = quarterdeck.build_html(homes, "T", bearings=bearings, allow_marks=True)
            self.assertEqual(page.count('data-mark-report="shared-home/amber-18"'), 0)
            disabled = 'button class="request-lavish" type="button" disabled>Mark reviewed</button>'
            self.assertGreaterEqual(page.count(disabled), len(bearings.reports))
            self.assertGreaterEqual(
                page.count("ID is shared by multiple homes; marking is disabled."), len(bearings.reports)
            )

    def test_incomplete_local_report_discovery_disables_mark_and_unmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            readable_home = BearingsTests().make_home(root / "readable")
            (readable_home / "data" / "later-report").mkdir()
            (readable_home / "data" / "later-report" / "report.md").write_text(
                "# Later report\n\nStill needs review.\n", encoding="utf-8"
            )
            incomplete_home = BearingsTests().make_home(root / "incomplete")
            (incomplete_home / "data" / "backlog.md").unlink()
            homes = [
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Readable", readable_home)),
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Incomplete", incomplete_home)),
            ]
            quarterdeck.assign_report_ids(homes)
            reviewed_report = homes[0].reports[0]
            quarterdeck.apply_review_marks(
                homes, {reviewed_report.report_id: reviewed_report.fingerprint}
            )
            bearings = quarterdeck.build_bearings(homes, use_snapshot=False)
            page = quarterdeck.build_html(homes, "T", bearings=bearings, allow_marks=True)
            self.assertIn('button class="request-lavish" type="button" disabled>Mark reviewed</button>', page)
            self.assertIn('button class="request-lavish" type="button" disabled>Unmark</button>', page)
            self.assertNotIn("data-mark-report=", page)
            self.assertEqual(
                page.count("Review changes are disabled while a selected local home is incomplete."), 2
            )

            static_page = quarterdeck.build_html(homes, "T", bearings=bearings)
            self.assertNotIn("after reading: quarterdeck reports mark-reviewed", static_page)
            self.assertIn(
                "Review changes are disabled while a selected local home is incomplete.", static_page
            )

    def test_server_reports_render_failure_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = quarterdeck.SiteCache(lambda: (_ for _ in ()).throw(ValueError("broken home")), 30)
            port = self.start(cache)
            status, _headers, body = self.fetch(port, "/")
            self.assertEqual(status, 500)
            self.assertIn("broken home", body)

    def test_loopback_server_rejects_nonlocal_host_on_all_pages(self) -> None:
        cache = quarterdeck.SiteCache(
            lambda: {"index.html": "private index", "report-secret.html": "private report"}, 30)
        port = self.start(cache)
        self.assertEqual(self.fetch(port, "/")[0], 200)
        for host in ("localhost", "localhost.", "127.0.0.1", "[::1]"):
            self.assertEqual(self.fetch(port, "/", headers={"Host": f"{host}:{port}"})[0], 200)
        for path in ("/", "/report-secret.html"):
            status, _headers, body = self.fetch(
                port, path, headers={"Host": f"attacker.example:{port}"})
            self.assertEqual(status, 403)
            self.assertNotIn("private", body)

    def test_serve_defaults_to_loopback_and_service_unit_runs_serve(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_CONFIG_HOME": temp}, clear=True):
            parser = quarterdeck.make_parser()
            args = parser.parse_args(["serve"])
            self.assertEqual(args.port, 8765)
            self.assertEqual(quarterdeck.bind_hosts(args), ["127.0.0.1"])
            unit = quarterdeck.service_unit(parser.parse_args(["service", "--port", "9100"]))
            directives = parse_systemd_unit(unit)
            command = directives["Service"]["ExecStart"][0]
            self.assertEqual(command[-4:], ["--host", "127.0.0.1", "--port", "9100"])
            multi = quarterdeck.service_unit(parser.parse_args(
                ["service", "--host", "127.0.0.1", "--host", "::1"]))
            command = parse_systemd_unit(multi)["Service"]["ExecStart"][0]
            self.assertEqual(command[-6:], ["--host", "127.0.0.1", "--host", "::1", "--port", "8765"])
            self.assertEqual(directives["Install"]["WantedBy"][0], ["default.target"])

    def test_service_unit_preserves_effective_home_and_custom_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_home = root / "${CONFIG}" / "shell-config"
            config_home.mkdir(parents=True)
            home = root / "selected-home"
            alternate_home = root / "${USER}" / "shell-home"
            config_path = config_home / "quarterdeck.json"
            config_path.write_text(json.dumps({"homes": [{"path": str(home)}]}), encoding="utf-8")
            data_override = root / "${DATA}" / "shell-data"
            state_home = root / "${STATE}" / "shell-state"
            env = {
                "XDG_CONFIG_HOME": str(config_home),
                "FM_HOME": str(alternate_home),
                "FM_DATA_OVERRIDE": str(data_override),
                "XDG_STATE_HOME": str(state_home),
            }
            with patch.dict(os.environ, env):
                parser = quarterdeck.make_parser()
                unit = quarterdeck.service_unit(parser.parse_args(["service"]))
                directives = parse_systemd_unit(unit)
                command = directives["Service"]["ExecStart"][0]
                unit_environment = directives["Service"]["Environment"]
                self.assertIn(["--config", str(config_path.resolve())], [command[index:index + 2]
                              for index in range(len(command) - 1)])
                self.assertNotIn(str(alternate_home.resolve()), command)
                self.assertNotIn(f"FM_DATA_OVERRIDE={data_override.resolve()}", unit_environment)
                self.assertIn(f"XDG_STATE_HOME={state_home.resolve()}", unit_environment)

                config_path.write_text(json.dumps({"page_title": "Selected"}), encoding="utf-8")
                with patch.object(quarterdeck.sys, "executable", str(root / "python${PYTHON}")):
                    unit = quarterdeck.service_unit(parser.parse_args(["service"]))
                directives = parse_systemd_unit(unit)
                command = directives["Service"]["ExecStart"][0]
                unit_environment = directives["Service"]["Environment"]
                self.assertIn(["--home", str(alternate_home.resolve())], [command[index:index + 2]
                              for index in range(len(command) - 1)])
                self.assertEqual(command[0], str(root / "python${PYTHON}"))
                self.assertIn(["--config", str(config_path.resolve())], [command[index:index + 2]
                              for index in range(len(command) - 1)])
                self.assertIn(f"FM_DATA_OVERRIDE={data_override.resolve()}", unit_environment)
                self.assertIn(f"XDG_STATE_HOME={state_home.resolve()}", unit_environment)

    def test_service_reports_config_errors_without_tracebacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "quarterdeck.json"
            config_path.write_text('{"unsupported": true}', encoding="utf-8")
            for write in (False, True):
                args = ["service", "--config", str(config_path)]
                if write:
                    args.append("--write")
                stderr = io.StringIO()
                with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                    result = quarterdeck.main(args)
                self.assertEqual(result, 2)
                self.assertIn("unsupported config key", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_url_reports_installed_service_addresses_and_default_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_home = Path(temp) / "config"
            unit_path = config_home / "systemd" / "user" / "quarterdeck.service"
            env = {"XDG_CONFIG_HOME": str(config_home), "HOME": temp}
            with patch.dict(os.environ, env, clear=True):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(quarterdeck.main(["url"]), 0)
                self.assertIn("http://127.0.0.1:8765/", output.getvalue())
                self.assertIn("no installed Quarterdeck service was found", output.getvalue())
                self.assertIn("A plain `quarterdeck serve` listens on the default address shown", output.getvalue())
                self.assertIn("custom --host/--port is not detected", output.getvalue())
                self.assertIn("quarterdeck serve", output.getvalue())

                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(quarterdeck.main(["url", "--json"]), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result["source"], "default")
                self.assertIn("No installed Quarterdeck service was found", result["note"])
                self.assertIn("custom --host/--port is not detected", result["note"])

                unit_path.parent.mkdir(parents=True)
                unit_path.write_text(quarterdeck.service_unit(
                    quarterdeck.make_parser().parse_args(
                        [
                            "service", "--host", "127.0.0.1", "--host", "::1",
                            "--host", "fe80::1%eth0", "--port", "9100",
                        ]
                    )
                ), encoding="utf-8")
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(quarterdeck.main(["url"]), 0)
                self.assertIn("http://[fe80::1%25eth0]:9100/", output.getvalue())

                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(quarterdeck.main(["url", "--json"]), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result["urls"], [
                    "http://127.0.0.1:9100/", "http://[::1]:9100/", "http://[fe80::1%25eth0]:9100/",
                ])
                self.assertEqual(result["hosts"], ["127.0.0.1", "::1", "fe80::1%eth0"])
                self.assertIn("quarterdeck.service", result["source"])

    def test_url_is_discoverable_through_command_index(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(quarterdeck.main(["help", "--json"]), 0)
        index = json.loads(output.getvalue())
        command = next(item for item in index["commands"] if item["name"] == "url")
        self.assertIn("how-to/serve-the-page.md", command["docs"])

    def test_server_options_reject_removed_aliases_and_invalid_ports(self) -> None:
        parser = quarterdeck.make_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["serve", "--bind", "127.0.0.1"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["service", "--bind", "127.0.0.1"])
        for command in ("serve", "service"):
            for value in ("-1", "0", "65536", "nan"):
                with self.subTest(command=command, port=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parser.parse_args([command, "--port", value])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["serve", "--cache", "-1"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["serve", "--refresh", "-1"])

    def test_service_rejects_control_characters_in_serialized_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_home = str(Path(temp) / "config")
            cases = [
                (["service", "--host", "127.0.0.1\nRestart=no"], {}),
                (["service", "--home", "/tmp/home\nRestart=no"], {}),
                (["service", "--home", "/tmp/home"], {"FM_DATA_OVERRIDE": "/tmp/data\nRestart=no"}),
                (["service", "--home", "/tmp/home"], {"XDG_STATE_HOME": "/tmp/state\nRestart=no"}),
            ]
            for args, environment in cases:
                env = {"HOME": temp, "XDG_CONFIG_HOME": config_home, **environment}
                with self.subTest(args=args, environment=environment), patch.dict(os.environ, env, clear=True), \
                        redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
                    result = quarterdeck.main(args)
                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("cannot contain control characters", stderr.getvalue())

    def test_service_write_refuses_a_symlink_unit_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_home = root / "config"
            unit = config_home / "systemd" / "user" / "quarterdeck.service"
            unit.parent.mkdir(parents=True)
            target = root / "unrelated.txt"
            target.write_text("preserve this file", encoding="utf-8")
            unit.symlink_to(target)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}), redirect_stderr(io.StringIO()):
                code = quarterdeck.main(["service", "--write"])
            self.assertEqual(code, 2)
            self.assertTrue(unit.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve this file")

    def test_service_write_goes_to_user_unit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {"XDG_CONFIG_HOME": str(Path(temp) / "config")}
            with patch.dict(os.environ, env), redirect_stdout(io.StringIO()):
                code = quarterdeck.main(["service", "--write"])
            self.assertEqual(code, 0)
            unit = Path(temp) / "config" / "systemd" / "user" / "quarterdeck.service"
            directives = parse_systemd_unit(unit.read_text(encoding="utf-8"))
            self.assertEqual(len(directives["Service"]["ExecStart"]), 1)
            self.assertEqual(directives["Service"]["ExecStart"][0][2], "serve")
            self.assertEqual(directives["Install"]["WantedBy"][0], ["default.target"])


RELEVANCE_BACKLOG = """# Backlog

## In flight

- [ ] harbor-build - Harbor build: raise the invented quay (repo: harbor-demo) (state: in_flight)

## Queued

- [ ] harbor-hold - Harbor hold: choose the quay colour (repo: harbor-demo) (held: yes) (hold: Raw note: red or blue.) (hold-kind: captain)
- [ ] tide-hold - Tide hold: pick the tide table (repo: harbor-demo) (held: yes) (hold: Raw note: pick one.) (hold-kind: captain)
- [ ] far-hold - Far hold: name the distant light (repo: faraway-demo) (held: yes) (hold: Raw note: pick one.) (hold-kind: captain)
- [ ] tide-note - Tide note: survey the tides (repo: harbor-demo)
"""


FAKE_INBOX = """#!/bin/sh
printf '%s\\n' "$@" >> "$QD_TEST_LOG"
printf 'home=%s\\n' "$FM_HOME" >> "$QD_TEST_LOG"
[ -z "$QD_TEST_FAIL" ] || exit "$QD_TEST_FAIL"
echo '{"outcome":"created"}'
"""


class DeskRelevanceTests(unittest.TestCase):
    """Discard, relevance, relations, the related section, and the artifact round trip."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.home = self.make_home()
        script = self.home / "bin" / "fm-inbox.sh"
        script.parent.mkdir()
        script.write_text(FAKE_INBOX, encoding="utf-8")
        script.chmod(0o755)
        self.log = self.root / "inbox.log"
        env = {"XDG_STATE_HOME": str(self.state), "HOME": str(self.root), "QD_TEST_LOG": str(self.log)}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_home(self) -> Path:
        home = self.root / "invented-home"
        data = home / "data"
        for name in ("harbor-hold", "tide-note", "lone-report"):
            (data / name).mkdir(parents=True)
            (data / name / "report.md").write_text(
                f"# {name.replace('-', ' ').title()} report\n\n## Recommendation\n\nGo with option A.\n\n## Evidence\n\nBody-only evidence line.\n",
                encoding="utf-8",
            )
        (data / "backlog.md").write_text(RELEVANCE_BACKLOG, encoding="utf-8")
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        items = [
            {"id": "harbor", "group": "decide", "title": "Quay colour", "ask": "Pick the quay colour.",
             "task": "harbor-hold", "touched_at": recent, "topic": "Quay"},
            {"id": "tide", "group": "decide", "title": "Tide table", "ask": "Pick the tide table.",
             "task": "tide-hold", "touched_at": old},
            {"id": "far", "group": "decide", "title": "Distant light", "ask": "Name the distant light.",
             "task": "far-hold", "touched_at": old},
        ]
        (data / "captain-board.json").write_text(
            json.dumps({"version": 1, "updated_at": recent, "items": items}), encoding="utf-8"
        )
        return home

    def cli(self, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "XDG_CONFIG_HOME": str(self.root / "config")}
        env.pop("FM_HOME", None)
        env.pop("FM_DATA_OVERRIDE", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "quarterdeck.py"), *args, "--home", str(self.home)],
            check=False, text=True, capture_output=True, env=env,
        )
        self.assertEqual(result.returncode, expect, result.stderr + result.stdout)
        return result

    def page(self) -> str:
        output = self.root / "out" / "index.html"
        self.cli("render", "--output", str(output), "--no-snapshot")
        return output.read_text(encoding="utf-8")

    def section(self, page: str, section_id: str) -> str:
        start = page.index(f'id="{section_id}"')
        return page[start:page.index("</details>", start)]

    def test_discard_is_not_reviewed_and_is_undone_by_undiscard(self) -> None:
        listed = self.cli("reports", "list").stdout
        self.assertIn("invented-home/lone-report\tneeds review\t", listed)
        self.cli("reports", "discard", "invented-home/lone-report")
        listed = self.cli("reports", "list").stdout
        self.assertIn("invented-home/lone-report\tdiscarded\t", listed)
        self.assertEqual(quarterdeck.load_review_marks(quarterdeck.review_state_path()), {})
        page = self.page()
        self.assertIn("Lone Report report", self.section(page, "discarded"))
        self.assertNotIn("Lone Report report", page.split('id="reviewed"')[0].split('id="reviews"')[1].split("</details>")[0])
        self.cli("reports", "undiscard", "invented-home/lone-report")
        self.assertIn("invented-home/lone-report\tneeds review\t", self.cli("reports", "list").stdout)

    def test_discard_and_reviewed_replace_each_other_and_content_change_returns_the_report(self) -> None:
        report = "invented-home/lone-report"
        self.cli("reports", "mark-reviewed", report)
        self.cli("reports", "discard", report)
        self.assertEqual(quarterdeck.load_review_marks(quarterdeck.review_state_path()), {})
        self.assertIn(f"{report}\tdiscarded\t", self.cli("reports", "list").stdout)
        self.cli("reports", "unmark-reviewed", report)  # clears only its own state
        self.assertIn(f"{report}\tdiscarded\t", self.cli("reports", "list").stdout)
        self.cli("reports", "mark-reviewed", report)
        self.assertIn(f"{report}\treviewed\t", self.cli("reports", "list").stdout)
        self.cli("reports", "discard", report)
        path = self.home / "data" / "lone-report" / "report.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nNew finding.\n", encoding="utf-8")
        self.assertIn(f"{report}\tneeds review\t", self.cli("reports", "list").stdout)

    def test_stale_reports_are_hidden_by_default_and_shown_in_their_own_section(self) -> None:
        report = "invented-home/lone-report"
        self.assertIn("\tcurrent", self.cli("reports", "list").stdout)  # unclassified means current
        self.cli("reports", "set-relevance", report, "stale", "--reason", "one-off, already decided")
        self.assertIn(f"{report}\tneeds review\tLone Report report\tinvented-home\tstale", self.cli("reports", "list").stdout)
        page = self.page()
        review_list = page.split('id="reviews"')[1].split("</details>")[0]
        self.assertNotIn("Lone Report report", review_list)
        stale = self.section(page, "stale-reports")
        self.assertIn("Lone Report report", stale)
        self.assertIn("one-off, already decided", stale)
        self.cli("reports", "set-relevance", report, "current")
        self.assertIn("Lone Report report", self.page().split('id="reviews"')[1].split("</details>")[0])
        self.cli("reports", "set-relevance", report, "maybe", expect=2)

    def test_stale_calls_leave_the_desk_and_bands(self) -> None:
        self.cli("calls", "set-relevance", "tide-hold", "stale", "--reason", "decided in chat")
        homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("invented-home", self.home))]
        quarterdeck.apply_review_marks(homes, quarterdeck.load_review_state(quarterdeck.review_state_path()))
        view = quarterdeck.board_view(homes)
        self.assertEqual([e.item.title for e in view.stale], ["Tide table"])
        self.assertEqual(view.stale[0].reason, "decided in chat")
        shown = [e.item.title for band in view.bands.values() for e in band]
        self.assertNotIn("Tide table", shown)
        page = self.page()
        self.assertIn("Marked stale", page)
        self.assertIn("decided in chat", page)
        listing = self.cli("calls", "list").stdout
        self.assertIn("invented-home/tide-hold\tstale\tstale\tTide table", listing)
        self.cli("calls", "set-relevance", "invented-home/tide-hold", "current")
        self.assertNotIn("Marked stale", self.page())
        self.cli("calls", "set-relevance", "no-such-task", "stale", expect=2)

    def test_related_section_is_collapsed_below_the_desk_and_ranked(self) -> None:
        page = self.page()
        self.assertLess(page.index('id="board-desk"'), page.index('id="related"'))
        self.assertIn('<details class="dashboard-section related" id="related"', page)
        self.assertNotIn(" open", page[page.index('id="related"'):page.index('id="related"') + 80])
        desk = page[page.index('id="board-desk"'):page.index('id="related"')]
        related = self.section(page, "related")
        self.assertNotIn("Tide table", desk)
        self.assertIn("Tide table", related)  # shares the project with the desk and in-flight work
        self.assertIn("same project: harbor-demo", related)
        self.assertNotIn("Distant light", related)
        self.assertNotIn("Lone Report report", related)
        self.cli("reports", "relate", "invented-home/lone-report", "--to", "harbor-build")
        related = self.section(self.page(), "related")
        self.assertIn("linked to Harbor build: raise the invented quay by Firstmate", related)
        self.assertLess(related.index("Lone Report report"), related.index("Tide table"))
        self.cli("reports", "relate", "invented-home/lone-report", "--to", "harbor-build", "--remove")
        self.assertNotIn("Lone Report report", self.section(self.page(), "related"))
        self.cli("calls", "relate", "far-hold", "--to", "harbor-hold")
        self.assertIn("Distant light", self.section(self.page(), "related"))

    def test_related_section_leaves_out_stale_and_discarded_reports(self) -> None:
        self.assertIn("Tide Note report", self.section(self.page(), "related"))
        self.cli("reports", "set-relevance", "invented-home/tide-note", "stale")
        self.assertNotIn("Tide Note report", self.section(self.page(), "related"))
        self.cli("reports", "set-relevance", "invented-home/tide-note", "current")
        self.cli("reports", "discard", "invented-home/tide-note")
        self.assertNotIn("Tide Note report", self.section(self.page(), "related"))

    def test_header_is_compact_and_the_desk_comes_first(self) -> None:
        page = self.page()
        self.assertNotIn("clamp(2.5rem", page)
        self.assertNotIn('class="kicker"', page)
        header = page[page.index("<header>"):page.index("</header>")]
        self.assertNotIn("A read-only view", header)
        self.assertLess(page.index("<header>"), page.index('id="board-desk"'))
        self.assertLess(page.index('id="board-desk"'), page.index("Reports waiting on your review"))

    def test_marks_json_is_imported_once_and_left_readable(self) -> None:
        report_path = self.home / "data" / "lone-report" / "report.md"
        fingerprint = quarterdeck.hashlib.sha256(report_path.read_bytes()).hexdigest()
        legacy = self.state / "quarterdeck" / "review-state" / "marks.json"
        legacy.parent.mkdir(parents=True)
        original = json.dumps({"version": 1, "reviewed": {"invented-home/lone-report": fingerprint}})
        legacy.write_text(original, encoding="utf-8")
        self.assertIn("invented-home/lone-report\treviewed\t", self.cli("reports", "list").stdout)
        self.assertTrue((legacy.parent / "state.db").is_file())
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)
        self.cli("reports", "unmark-reviewed", "invented-home/lone-report")
        self.assertIn("invented-home/lone-report\tneeds review\t", self.cli("reports", "list").stdout)  # not re-imported
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)

    def test_invalid_legacy_marks_are_refused_not_dropped(self) -> None:
        legacy = self.state / "quarterdeck" / "review-state" / "marks.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("{not json", encoding="utf-8")
        result = self.cli("reports", "list", expect=2)
        self.assertIn("could not read review state", result.stderr)

    def test_state_database_is_private(self) -> None:
        self.cli("reports", "discard", "invented-home/lone-report")
        db = self.state / "quarterdeck" / "review-state" / "state.db"
        self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(db.parent.stat().st_mode), 0o700)
        self.assertFalse(list(self.home.rglob("*.db")))
        board = json.loads((self.home / "data" / "captain-board.json").read_text(encoding="utf-8"))
        self.assertEqual(len(board["items"]), 3)  # the home's board is untouched by relevance and discards

    def test_export_writes_the_snapshot_and_nothing_else(self) -> None:
        self.cli("reports", "set-relevance", "invented-home/lone-report", "stale", "--reason", "one-off")
        self.cli("calls", "set-relevance", "far-hold", "stale")
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(
            set(snapshot),
            {"version", "title", "generated_at", "session", "calls", "reports", "related", "reactions", "delivered"},
        )
        bands = {call["id"]: call["band"] for call in snapshot["calls"]}
        self.assertEqual(bands["invented-home/harbor-hold"], "desk")
        self.assertEqual(bands["invented-home/tide-hold"], "parked")
        self.assertEqual(bands["invented-home/far-hold"], "stale")
        reports = {report["id"]: report for report in snapshot["reports"]}
        self.assertEqual(reports["invented-home/lone-report"]["relevance"], "stale")
        self.assertRegex(reports["invented-home/lone-report"]["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(reports["invented-home/lone-report"]["markdown"], "")
        self.assertIn("Body-only evidence", reports["invented-home/tide-note"]["markdown"])
        self.assertNotIn(str(self.home), json.dumps(snapshot))
        self.assertTrue(any(item["id"] == "invented-home/tide-hold" for item in snapshot["related"]))

    def test_artifact_page_embeds_the_snapshot_safely(self) -> None:
        board = self.home / "data" / "captain-board.json"
        data = json.loads(board.read_text(encoding="utf-8"))
        data["items"][0]["title"] = "</script><script>alert(1)</script>"
        board.write_text(json.dumps(data), encoding="utf-8")
        output = self.root / "out" / "artifact.html"
        self.cli("export", "--no-snapshot", "--artifact", str(output))
        page = output.read_text(encoding="utf-8")
        self.assertNotIn("__QUARTERDECK_", page)
        self.assertEqual(page.count("</script>"), 2)  # the snapshot block and the page script only
        payload = re.search(r'id="qd-snapshot">(.*?)</script>', page, re.S).group(1)
        embedded = json.loads(payload)
        self.assertIn("</script><script>alert(1)</script>", [c["title"] for c in embedded["calls"]])
        self.assertIn("window.claude", page)
        self.assertNotIn("http://", page.replace("http://www.w3.org", ""))
        inside = self.cli("export", "--no-snapshot", "--artifact", str(self.home / "artifact.html"), expect=2)
        self.assertIn("outside the Quarterdeck checkout", inside.stderr)

    def test_import_applies_artifact_actions_and_skips_changed_reports(self) -> None:
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        reports = {r["id"]: r for r in snapshot["reports"]}
        lone, tide = reports["invented-home/lone-report"], reports["invented-home/tide-note"]
        actions = {"version": 1, "actions": [
            {"report_id": lone["id"], "action": "mark", "fingerprint": lone["fingerprint"], "at": "2026-10-12T10:00:00Z"},
            {"report_id": lone["id"], "action": "discard", "fingerprint": lone["fingerprint"], "at": "2026-10-12T11:00:00Z"},
            {"report_id": tide["id"], "action": "mark", "fingerprint": "0" * 64, "at": "2026-10-12T10:00:00Z"},
        ]}
        path = self.root / "actions.json"
        path.write_text(json.dumps(actions), encoding="utf-8")
        dry = self.cli("import", str(path), "--dry-run").stdout
        self.assertIn("Would apply 2 of 3", dry)
        self.assertIn("needs review", self.cli("reports", "list").stdout.split("lone-report")[1].split("\n")[0])
        result = self.cli("import", str(path)).stdout
        self.assertIn("Applied 2 of 3", result)
        self.assertIn("skipped: invented-home/tide-note (mark): report changed", result)
        listed = self.cli("reports", "list").stdout
        self.assertIn("invented-home/lone-report\tdiscarded\t", listed)  # the later action won
        self.assertIn("invented-home/tide-note\tneeds review\t", listed)
        bare = self.root / "bare.json"  # a bare list of database documents is accepted too
        bare.write_text(json.dumps([{"id": "x", "data": {
            "report_id": lone["id"], "action": "undiscard", "fingerprint": lone["fingerprint"], "at": "2026-10-12T12:00:00Z",
        }}]), encoding="utf-8")
        self.cli("import", str(bare))
        self.assertIn("invented-home/lone-report\tneeds review\t", self.cli("reports", "list").stdout)

    def test_import_rejects_malformed_files(self) -> None:
        for name, content in {
            "not-json": "{",
            "unknown-action": json.dumps([{"report_id": "a/b", "action": "delete", "fingerprint": "0" * 64}]),
            "bad-fingerprint": json.dumps([{"report_id": "a/b", "action": "mark", "fingerprint": "abc"}]),
            "wrong-shape": json.dumps({"actions": "mark"}),
        }.items():
            with self.subTest(name):
                path = self.root / f"{name}.json"
                path.write_text(content, encoding="utf-8")
                self.cli("import", str(path), expect=2)
        self.assertFalse((self.state / "quarterdeck" / "review-state" / "state.db").exists())

    def test_export_omits_bodies_for_reviewed_discarded_and_stale_reports(self) -> None:
        self.cli("reports", "mark-reviewed", "invented-home/harbor-hold")
        self.cli("reports", "discard", "invented-home/tide-note")
        self.cli("reports", "set-relevance", "invented-home/lone-report", "stale")
        reports = json.loads(self.cli("export", "--no-snapshot").stdout)["reports"]
        self.assertTrue(reports)
        self.assertTrue(all(report["markdown"] == "" for report in reports))

    def test_no_board_calls_remain_available_to_listing_related_and_export(self) -> None:
        (self.home / "data" / "captain-board.json").unlink()
        self.assertIn("invented-home/tide-hold", self.cli("calls", "list").stdout)
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        self.assertEqual({call["id"] for call in snapshot["calls"]}, {
            "invented-home/harbor-hold", "invented-home/tide-hold", "invented-home/far-hold",
        })
        self.assertIn("invented-home/tide-hold", {item["id"] for item in snapshot["related"]})
        self.cli("calls", "set-relevance", "tide-hold", "stale")
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        self.assertEqual(next(call["band"] for call in snapshot["calls"] if call["id"].endswith("/tide-hold")), "stale")

    def test_routed_calls_share_keys_across_board_backlog_bearings_and_export(self) -> None:
        child = quarterdeck.HomeSnapshot(
            quarterdeck.HomeSpec("Field guide", None, route_id="wren"),
            backlog_markdown="- [ ] task - Choose a bird (held: yes) (hold-kind: captain)\n",
        )
        parent = quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("invented-home", self.home))
        parent.children = [child]
        parent.board.items = [quarterdeck.BoardItem(
            id="route", group="decide", title="Bird", ask="Choose", task="wren/task",
        )]
        homes = [parent]
        key = "field-guide/task"
        self.assertEqual(quarterdeck.resolve_call_key(homes, "task"), key)
        state = quarterdeck.ReviewState(relevance={("call", key): ("stale", "settled")})
        quarterdeck.apply_review_marks(homes, state)
        view = quarterdeck.board_view(homes)
        self.assertEqual([entry.key for entry in view.stale], [key])
        self.assertNotIn(key, {entry.key for band in view.bands.values() for entry in band})
        self.assertNotIn("task", {item.item_id for item in quarterdeck.build_bearings(homes, use_snapshot=False).call})
        payload = {**SNAPSHOT_JSON, "decisions_open": [
            {"owner": owner, "key": task, "summary": "Bird", "verb": "decide"}
            for owner, task in [("wren", "task"), ("wren", "wren/task"), ("(main)", "wren/task")]
        ]}
        bearings = quarterdeck.build_bearings(homes, runner=lambda *_args: (payload, ""))
        self.assertFalse(bearings.call)
        exported = quarterdeck.build_snapshot(homes, "Q", datetime.now(timezone.utc))
        self.assertEqual(next(call["band"] for call in exported["calls"] if call["id"] == key), "stale")
        state.relevance.clear()
        state.relations[("call", key)] = ["invented-home/harbor-build"]
        quarterdeck.apply_review_marks(homes, state)
        self.assertIn(key, {item.key for item in quarterdeck.related_items(homes, quarterdeck.board_view(homes))})
        parent.board = None
        exported = quarterdeck.build_snapshot(homes, "Q", datetime.now(timezone.utc))
        self.assertEqual(next(call["related_to"] for call in exported["calls"] if call["id"] == key), ["invented-home/harbor-build"])
        self.assertIn(key, {item["id"] for item in exported["related"]})

    def test_related_matching_preserves_home_identity_for_tasks_links_and_topics(self) -> None:
        a = quarterdeck.HomeSnapshot(quarterdeck.HomeSpec("A", None), backlog_markdown="""# Backlog
## In flight
- [ ] build - A active (state: in_flight) (waiting_on: b/dep)
## Queued
- [ ] desk - A desk (held: yes) (hold-kind: captain)
""")
        b = quarterdeck.HomeSnapshot(quarterdeck.HomeSpec("B", None), backlog_markdown="""# Backlog
## Queued
- [ ] build - B held (held: yes) (hold-kind: captain) (waiting_on: a/build)
- [ ] dep - B dependency (held: yes) (hold-kind: captain)
- [ ] desk - B desk (held: yes) (hold-kind: captain)
""")
        now = datetime.now(timezone.utc)
        a.board = quarterdeck.CaptainBoard(Path("board-a"), items=[quarterdeck.BoardItem(
            id="desk", group="decide", title="A desk", ask="Choose", task="desk", topic="A topic", touched_at=now,
        )])
        b.board = quarterdeck.CaptainBoard(Path("board-b"), items=[quarterdeck.BoardItem(
            id="desk", group="decide", title="B desk", ask="Choose", task="desk", topic="B topic",
        )])
        for snapshot in (a, b):
            snapshot.reports = [quarterdeck.Report("Build report", Path("build/report.md"), None, "Body", "0" * 64)]
        homes = [a, b]
        quarterdeck.apply_review_marks(homes, {})
        related = {(item.kind, item.key): item for item in quarterdeck.related_items(homes, quarterdeck.board_view(homes, now))}
        self.assertIn(("call", "b/build"), related)
        self.assertIn("waits on A active", related[("call", "b/build")].why)
        self.assertFalse(any(reason.startswith("report for") for reason in related[("report", "b/build")].why))
        self.assertIn(("call", "b/dep"), related)
        self.assertNotIn(("call", "b/desk"), related)
        self.assertIn("report for A active", related[("report", "a/build")].why)
        b.reports.append(quarterdeck.Report("Desk report", Path("desk/report.md"), None, "Body", "1" * 64))
        state = quarterdeck.ReviewState(relations={("call", "b/desk"): ["b/build"]})
        quarterdeck.apply_review_marks(homes, state)
        related = {item.key for item in quarterdeck.related_items(homes, quarterdeck.board_view(homes, now))}
        self.assertNotIn("b/desk", related)
        state.relations[("call", "b/desk")] = ["a/build"]
        quarterdeck.apply_review_marks(homes, state)
        related = {item.key for item in quarterdeck.related_items(homes, quarterdeck.board_view(homes, now))}
        self.assertIn("b/desk", related)

    def test_import_skips_actions_superseded_by_the_snapshot_cutoff(self) -> None:
        report = "invented-home/lone-report"
        fingerprint = next(r["fingerprint"] for r in json.loads(self.cli("export", "--no-snapshot").stdout)["reports"] if r["id"] == report)
        self.cli("reports", "mark-reviewed", report)
        self.cli("reports", "unmark-reviewed", report)
        path = self.root / "actions.json"
        homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("invented-home", self.home))]
        when = quarterdeck.parse_board_time("2026-10-12T12:00:00.900000Z")
        cutoff = quarterdeck.build_snapshot(homes, "Q", when)["generated_at"]
        self.assertEqual(quarterdeck.parse_board_time(cutoff), when)
        path.write_text(json.dumps({"version": 1, "generated_at": cutoff, "actions": [{
            "report_id": report, "action": "mark", "fingerprint": fingerprint, "state": "reviewed",
            "at": "2026-10-12T12:00:00.100Z",
        }]}), encoding="utf-8")
        self.assertIn("Applied 0 of 0", self.cli("import", str(path)).stdout)
        self.assertIn(f"{report}\tneeds review", self.cli("reports", "list").stdout)

    def test_inverse_actions_preserve_a_concurrently_replaced_state(self) -> None:
        homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("invented-home", self.home))]
        quarterdeck.apply_review_marks(homes, {})
        report = next(r for r in homes[0].reports if r.report_id.endswith("/lone-report"))
        path = quarterdeck.review_state_path()
        write = quarterdeck.write_state
        for initial, inverse, replacement in [("reviewed", "unmark", "discarded"), ("discarded", "undiscard", "reviewed")]:
            with self.subTest(action=inverse):
                quarterdeck.update_report_state(path, report.report_id, initial, report.fingerprint)

                def interleave(state_path, statements):
                    if statements[0][0].startswith("DELETE"):
                        quarterdeck.update_report_state(path, report.report_id, replacement, report.fingerprint)
                    write(state_path, statements)

                with patch.object(quarterdeck, "write_state", side_effect=interleave):
                    result = quarterdeck.apply_report_action(homes, path, report.report_id, inverse, report.fingerprint)
                self.assertEqual(quarterdeck.load_review_state(path).reports[report.report_id][0], replacement)
                self.assertEqual(result.state, replacement)

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the generated artifact")
    def test_artifact_click_sequences_download_resulting_states_and_read_report_bodies(self) -> None:
        self.cli("reports", "mark-reviewed", "invented-home/lone-report")
        self.cli("reports", "discard", "invented-home/tide-note")
        report_path = self.home / "data" / "harbor-hold" / "report.md"
        report_path.write_text("# Readable report\n\nEvidence <script>alert(1)</script>\n", encoding="utf-8")
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        snapshot["generated_at"] = "2000-01-01T00:00:00Z"
        page = quarterdeck.artifact_page(snapshot)
        harness = r"""
const assert = require('node:assert/strict');
const page = require('node:fs').readFileSync(0, 'utf8');
const payload = page.match(/<script type="application\/json" id="qd-snapshot">([\s\S]*?)<\/script>/)[1];
const snapshot = JSON.parse(payload);
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  setAttribute(key, value) { this.attrs[key] = value; if (key === 'id') this.id = value; }
  appendChild(child) { this.children.push(child); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(tag) { return this.children.flatMap(c => [ ...(c.tag === tag ? [c] : []), ...c.querySelectorAll(tag) ]); }
}
const elements = Object.fromEntries(['qd-snapshot', 'root', 'title', 'generated', 'sync'].map(id => [id, new Element('div')]));
elements['qd-snapshot'].textContent = payload;
global.document = {
  createElement: tag => new Element(tag),
  getElementById: id => elements[id] || elements.root.querySelectorAll('details').concat(elements.root.querySelectorAll('section')).find(e => e.id === id)
};
const reports = Object.fromEntries(snapshot.reports.map(r => [r.id, r]));
const lone = reports['invented-home/lone-report'], tide = reports['invented-home/tide-note'], harbor = reports['invented-home/harbor-hold'];
const docs = [{report_id: harbor.id, action: 'mark', state: 'reviewed', fingerprint: harbor.fingerprint, at: '1999-12-31T00:00:00Z'},
  {report_id: tide.id, action: 'mark', state: 'reviewed', fingerprint: '0'.repeat(64), at: '2001-01-01T00:00:00Z'},
  {report_id: 'unknown/report', action: 'mark', state: 'reviewed', fingerprint: '0'.repeat(64), at: '2001-01-01T00:00:00Z'}];
let downloaded;
const saved = {};
const db = {
  collection: () => ({onSnapshot: callback => callback({docs: docs.map(data => ({data: () => data}))})}),
  doc: () => ({set: data => {saved[data.report_id] = data; return Promise.resolve();}})
};
global.claude = {use: async capability => capability === 'db' ? db : {save: async data => {downloaded = JSON.parse(data.data);}}};
global.window = {claude};
const script = page.match(/<script>([\s\S]*?)<\/script>/)[1];
require('node:vm').runInThisContext(script);
const flush = async () => {for (let i = 0; i < 5; i++) await Promise.resolve();};
const buttons = () => elements.root.querySelectorAll('button');
async function click(report, text) {
  const card = elements.root.querySelectorAll('article').find(c => c.children.some(n => n.tag === 'h3' && n.textContent === report.title) && c.querySelectorAll('button').some(b => b.textContent === text));
  assert.ok(card, report.id + ': ' + text);
  card.querySelectorAll('button').find(b => b.textContent === text).listeners.click();
  await flush();
}
(async () => {
  await flush();
  assert.equal(buttons().filter(b => b.textContent.startsWith('Download')).length, 0);
  for (const section of ['reports', 'related']) {
    const body = document.getElementById(section).querySelectorAll('pre').find(e => e.textContent === harbor.markdown);
    assert.ok(body, 'report body in ' + section);
  }
  assert.equal(elements.root.querySelectorAll('script').length, 0);
  await click(lone, 'Unmark'); await click(lone, 'Discard'); await click(lone, 'Undiscard');
  await click(tide, 'Undiscard'); await click(tide, 'Mark reviewed'); await click(tide, 'Unmark');
  for (const report of [lone, tide]) {
    assert.equal(saved[report.id].state, 'needs review');
    assert.ok(document.getElementById('reports').textContent.includes(report.title));
  }
  const download = buttons().find(b => b.textContent.startsWith('Download'));
  assert.ok(download.textContent.startsWith('Download 2 recorded actions'));
  download.listeners.click(); await flush();
  assert.deepEqual(downloaded.actions.map(a => a.report_id).sort(), [lone.id, tide.id].sort());
  assert.equal(downloaded.generated_at, snapshot.generated_at);
  process.stdout.write(JSON.stringify(downloaded));
})().catch(error => {process.stderr.write(error.stack); process.exitCode = 1;});
"""
        result = subprocess.run(["node", "-e", harness], input=page, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.root / "actions.json"
        actions.write_text(result.stdout, encoding="utf-8")
        self.cli("import", str(actions))
        listed = self.cli("reports", "list").stdout
        self.assertIn("invented-home/lone-report\tneeds review", listed)
        self.assertIn("invented-home/tide-note\tneeds review", listed)

    def test_served_page_discards_through_the_mark_endpoint(self) -> None:
        import argparse
        import http.client
        from http.server import ThreadingHTTPServer
        args = argparse.Namespace(home=str(self.home), config=None, title=None, no_snapshot=True)
        cache = quarterdeck.SiteCache(quarterdeck.site_builder(args, True), 30)
        handler = quarterdeck.make_handler(
            cache, lambda report_id, action, fingerprint: quarterdeck.set_report_action(args, report_id, action, fingerprint)
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        port = server.server_address[1]

        def request(method: str, path: str, body: object = None) -> tuple[int, str]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            connection.request(method, path, json.dumps(body) if body is not None else None,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            text = response.read().decode("utf-8")
            connection.close()
            return response.status, text

        page = request("GET", "/")[1]
        match = re.search(
            r'data-mark-report="(invented-home/lone-report)" data-mark-fingerprint="([0-9a-f]{64})" data-mark-action="discard"',
            page,
        )
        self.assertIsNotNone(match)
        report_id, fingerprint = match.groups()
        status, body = request("POST", quarterdeck.MARK_ENDPOINT,
                               {"report_id": report_id, "fingerprint": fingerprint, "action": "discard"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {"report_id": report_id, "reviewed": False, "state": "discarded"})
        status, body = request("POST", quarterdeck.MARK_ENDPOINT,
                               {"report_id": report_id, "fingerprint": fingerprint, "action": "unmark"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["state"], "discarded")
        request("POST", quarterdeck.MARK_ENDPOINT,
                {"report_id": report_id, "fingerprint": fingerprint, "action": "mark"})
        status, body = request("POST", quarterdeck.MARK_ENDPOINT,
                               {"report_id": report_id, "fingerprint": fingerprint, "action": "undiscard"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["state"], "reviewed")
        self.assertTrue(json.loads(body)["reviewed"])
        request("POST", quarterdeck.MARK_ENDPOINT,
                {"report_id": report_id, "fingerprint": fingerprint, "action": "discard"})
        self.assertEqual(quarterdeck.load_review_marks(quarterdeck.review_state_path()), {})
        page = request("GET", "/")[1]
        self.assertIn('data-mark-action="undiscard"', page)
        status, _ = request("POST", quarterdeck.MARK_ENDPOINT,
                            {"report_id": report_id, "fingerprint": fingerprint, "action": "banish"})
        self.assertEqual(status, 400)
        status, _ = request("POST", quarterdeck.MARK_ENDPOINT,
                            {"report_id": report_id, "fingerprint": "0" * 64, "action": "undiscard"})
        self.assertEqual(status, 409)


    def logged(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def review_state(self) -> "quarterdeck.ReviewState":
        return quarterdeck.load_review_state(quarterdeck.review_state_path())

    def serve(self):
        import argparse
        from http.server import ThreadingHTTPServer
        args = argparse.Namespace(home=str(self.home), config=None, title=None, no_snapshot=True)
        cache = quarterdeck.SiteCache(quarterdeck.site_builder(args, True), 30)
        handler = quarterdeck.make_handler(
            cache, lambda *a: quarterdeck.set_report_action(args, *a),
            lambda raw: quarterdeck.send_reaction(args, raw),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        port = server.server_address[1]

        def request(method: str, path: str, body: object = None, headers: dict[str, str] | None = None):
            import http.client
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            sent = {"Content-Type": "application/json", **(headers or {})}
            connection.request(method, path, json.dumps(body) if body is not None else None, sent)
            response = connection.getresponse()
            text = response.read().decode("utf-8")
            connection.close()
            return response.status, text
        return request

    def test_served_page_shows_reactions_on_calls_related_items_and_reports_only_with_marks_enabled(self) -> None:
        request = self.serve()
        page = request("GET", "/")[1]
        self.assertGreaterEqual(page.count('data-reaction-kind="call"'), 2)
        self.assertIn('data-reaction-kind="report"', page)
        for name in quarterdeck.REACTION_LABELS:
            self.assertIn(f'data-reaction-choice="{name}"', page)
        self.assertIn('data-reaction-item="invented-home/harbor-hold"', page)
        self.assertIn('data-reaction-item="invented-home/lone-report"', page)
        related = page[page.index('id="related"'):]
        self.assertIn('data-reaction-kind="call"', related.split("</details>")[0])
        self.assertNotIn("data-reaction-kind", self.page())  # a rendered file has no server to send to

    def test_sending_a_reaction_delivers_one_note_records_it_and_shows_it_on_the_card(self) -> None:
        request = self.serve()
        status, body = request("POST", quarterdeck.REACTION_ENDPOINT, {
            "id": "page-0001", "kind": "call", "item_id": "invented-home/harbor-hold", "reaction": "approve",
            "text": "Go  with\nteal.",
        })
        self.assertEqual(status, 200, body)
        sent = json.loads(body)
        self.assertEqual((sent["task"], sent["reaction"], sent["text"], sent["source"]),
                         ("harbor-hold", "approve", "Go with teal.", "page"))
        lines = self.logged()
        self.assertEqual(sent["id"], "page-0001")
        self.assertEqual(lines[:5], ["note", "--request-id", "qd-page-0001", "--json", "--"])
        self.assertEqual(len(lines), 7)  # five option lines, one note body, then the home
        note = lines[5]
        for part in ("Approve", "call invented-home/harbor-hold", "task harbor-hold", sent["at"], '"Go with teal."'):
            self.assertIn(part, note)
        self.assertEqual(lines[6], f"home={self.home}")
        self.assertEqual(self.review_state().reactions[("call", "invented-home/harbor-hold")]["id"], sent["id"])
        page = request("GET", "/")[1]
        self.assertIn("Latest reaction: Approve: Go with teal. (from this page", page)

    def test_reports_can_be_reacted_to_and_a_reply_needs_no_reaction(self) -> None:
        request = self.serve()
        status, body = request("POST", quarterdeck.REACTION_ENDPOINT, {
            "id": "page-0002", "kind": "report", "item_id": "invented-home/lone-report", "reaction": "",
            "text": "Why option A?",
        })
        self.assertEqual(status, 200, body)
        self.assertIn("reply on report invented-home/lone-report (task lone-report)", self.logged()[5])
        self.assertIn("Latest reaction: Why option A?", request("GET", "/")[1])

    def test_a_report_reaction_names_the_backlog_task_that_owns_the_report(self) -> None:
        backlog = self.home / "data" / "backlog.md"
        backlog.write_text(backlog.read_text(encoding="utf-8")
                           + "- [ ] lone-report - Lone report: see data/tide-note/report.md (repo: harbor-demo)\n"
                           + "- [ ] harbor-choice - Harbor choice: see data/lone-report/report.md (repo: harbor-demo)\n",
                           encoding="utf-8")
        request = self.serve()
        status, body = request("POST", quarterdeck.REACTION_ENDPOINT, {
            "id": "page-0005", "kind": "report", "item_id": "invented-home/lone-report", "reaction": "approve", "text": "",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["task"], "harbor-choice")
        self.assertIn("(task harbor-choice)", self.logged()[5])

    def test_reaction_route_refuses_bad_origin_bad_bodies_unknown_items_and_failed_delivery(self) -> None:
        request = self.serve()
        good = {"id": "page-0003", "kind": "call", "item_id": "invented-home/harbor-hold", "reaction": "hold", "text": ""}
        self.assertEqual(request("POST", quarterdeck.REACTION_ENDPOINT, good, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(request("POST", quarterdeck.REACTION_ENDPOINT, good, {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(request("POST", quarterdeck.REACTION_ENDPOINT, good, {"Content-Type": "text/plain"})[0], 415)
        for body in ({**good, "reaction": "banish"}, {**good, "reaction": "", "text": ""}, {**good, "kind": "task"},
                     {**good, "text": "x" * 1001}, {**good, "extra": "1"}, {"kind": "call"}, {**good, "text": 5},
                     {**good, "id": "bad id"}, {**good, "id": "short"}, {k: v for k, v in good.items() if k != "id"}):
            self.assertEqual(request("POST", quarterdeck.REACTION_ENDPOINT, body)[0], 400, body)
        self.assertEqual(request("POST", quarterdeck.REACTION_ENDPOINT, {**good, "item_id": "invented-home/nope"})[0], 404)
        self.assertEqual(request("POST", quarterdeck.REACTION_ENDPOINT,
                                 {**good, "kind": "report", "item_id": "invented-home/nope"})[0], 404)
        self.assertEqual(self.logged(), [])
        with patch.dict(os.environ, {"QD_TEST_FAIL": "1"}):
            status, body = request("POST", quarterdeck.REACTION_ENDPOINT, good)
        self.assertEqual(status, 502, body)
        self.assertEqual(self.review_state().reactions, {})  # an undelivered reaction is not recorded
        with patch.dict(os.environ, {"QD_TEST_FAIL": "3"}):
            self.assertIn("could not wake", request("POST", quarterdeck.REACTION_ENDPOINT, good)[1])
        self.assertEqual(request("POST", quarterdeck.REACTION_ENDPOINT, good)[0], 200)
        request_ids = [line for line in self.logged() if line.startswith("qd-")]
        self.assertEqual(request_ids, ["qd-page-0003"] * 3)  # every retry reuses the page's id, so fm-inbox dedupes it

    def test_reaction_route_is_absent_without_marks_and_a_home_without_the_script_cannot_deliver(self) -> None:
        from http.server import ThreadingHTTPServer
        import http.client
        read_only = ThreadingHTTPServer(
            ("127.0.0.1", 0), quarterdeck.make_handler(quarterdeck.SiteCache(lambda: {"index.html": "ok"}))
        )
        threading.Thread(target=read_only.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (read_only.shutdown(), read_only.server_close()))
        connection = http.client.HTTPConnection("127.0.0.1", read_only.server_address[1], timeout=10)
        connection.request("POST", quarterdeck.REACTION_ENDPOINT, "{}", {"Content-Type": "application/json"})
        self.assertEqual(connection.getresponse().status, 405)
        connection.close()
        (self.home / "bin" / "fm-inbox.sh").unlink()
        request = self.serve()
        status, body = request("POST", quarterdeck.REACTION_ENDPOINT, {
            "id": "page-0004", "kind": "call", "item_id": "invented-home/harbor-hold", "reaction": "hold", "text": "",
        })
        self.assertEqual(status, 502)
        self.assertIn("no executable bin/fm-inbox.sh", body)
        self.assertNotIn("data-reaction-kind", self.page())

    def test_import_delivers_each_artifact_reaction_once(self) -> None:
        def doc(reaction_id: str, item: str, kind: str = "call", **extra: object) -> dict[str, object]:
            return {"version": 1, "id": reaction_id, "item_id": item, "kind": kind, "task": "ignored",
                    "reaction": "not-now", "text": "later", "at": "2026-10-12T10:00:00Z", "source": "artifact", **extra}
        documents = [
            {"id": "x", "data": doc("react-0002", "invented-home/tide-hold", at="2026-10-12T11:00:00Z")},
            doc("react-0001", "invented-home/harbor-hold"),
            doc("react-0003", "invented-home/nowhere"),
            doc("react-0004", "invented-home/lone-report", "report", reaction="", text="Which option?"),
            doc("bad id", "invented-home/harbor-hold"),
            doc("react-0005", "invented-home/harbor-hold", reaction="banish"),
        ]
        path = self.root / "reactions.json"
        path.write_text(json.dumps({"version": 1, "reactions": documents}), encoding="utf-8")
        dry = self.cli("reactions", "import", str(path), "--dry-run").stdout
        self.assertIn("Would deliver 3 of 6", dry)
        self.assertEqual(self.logged(), [])
        result = self.cli("reactions", "import", str(path)).stdout
        self.assertIn("Delivered 3 of 6", result)
        self.assertIn("invented-home/nowhere", result)
        self.assertEqual(result.count("skipped:"), 3)
        lines = self.logged()
        request_ids = [line for line in lines if line.startswith("qd-")]
        self.assertEqual(request_ids, ["qd-react-0001", "qd-react-0004", "qd-react-0002"])  # oldest first
        self.assertIn("task harbor-hold", lines[lines.index("qd-react-0001") + 3])
        self.assertIn("(task lone-report)", lines[lines.index("qd-react-0004") + 3])
        again = self.cli("reactions", "import", str(path)).stdout
        self.assertIn("Delivered 0 of 6", again)
        self.assertIn("react-0001 (call invented-home/harbor-hold): already delivered", again)
        self.assertEqual(len([line for line in self.logged() if line.startswith("qd-")]), 3)
        state = self.review_state()
        self.assertEqual(state.reactions[("call", "invented-home/tide-hold")]["source"], "artifact")
        self.assertEqual({i for ids in state.delivered.values() for i in ids}, {"react-0001", "react-0002", "react-0004"})
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        self.assertEqual({r["item_id"] for r in snapshot["reactions"]},
                         {"invented-home/tide-hold", "invented-home/harbor-hold", "invented-home/lone-report"})
        self.assertEqual(set(snapshot["delivered"]), {"react-0001", "react-0002", "react-0004"})

    def test_reaction_times_are_normalized_so_the_latest_reaction_is_the_newest(self) -> None:
        documents = [
            {"id": "react-late", "item_id": "invented-home/harbor-hold", "kind": "call", "reaction": "approve",
             "text": "", "at": "2026-10-12T10:00:00.500Z"},
            {"id": "react-early", "item_id": "invented-home/harbor-hold", "kind": "call", "reaction": "hold",
             "text": "", "at": "2026-10-12T11:00:00+01:00"},
        ]
        path = self.root / "reactions.json"
        path.write_text(json.dumps(documents), encoding="utf-8")
        self.assertIn("Delivered 2 of 2", self.cli("reactions", "import", str(path)).stdout)
        state = self.review_state()
        latest = state.reactions[("call", "invented-home/harbor-hold")]
        self.assertEqual((latest["id"], latest["at"]), ("react-late", "2026-10-12T10:00:00.500Z"))
        self.assertEqual(state.delivered[("call", "invented-home/harbor-hold")], ["react-early", "react-late"])

    def test_export_carries_only_reactions_for_items_the_artifact_shows(self) -> None:
        quarterdeck.store_reaction(quarterdeck.review_state_path(), {
            "id": "react-elsewhere", "item_id": "other-home/private-task", "kind": "call", "task": "private-task",
            "reaction": "", "text": "private reply", "at": "2026-10-12T10:00:00.000Z", "source": "page",
        })
        quarterdeck.store_reaction(quarterdeck.review_state_path(), {
            "id": "react-shown", "item_id": "invented-home/harbor-hold", "kind": "call", "task": "harbor-hold",
            "reaction": "hold", "text": "", "at": "2026-10-12T10:00:00.000Z", "source": "page",
        })
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        self.assertEqual([r["item_id"] for r in snapshot["reactions"]], ["invented-home/harbor-hold"])
        self.assertEqual(snapshot["delivered"], ["react-shown"])
        self.assertNotIn("private reply", quarterdeck.artifact_page(snapshot))

    def test_call_reactions_go_to_the_home_that_owns_the_call_and_refuse_ambiguous_labels(self) -> None:
        second = self.root / "second-home"
        (second / "data").mkdir(parents=True)
        (second / "data" / "backlog.md").write_text(
            "# Backlog\n\n## Queued\n\n- [ ] only-second - Only second: a task (repo: harbor-demo)\n"
            "- [ ] tide-note - Tide note: a second copy (repo: harbor-demo)\n",
            encoding="utf-8",
        )
        snapshots = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Dock", home, None))
                     for home in (self.home, second)]
        owner, task = quarterdeck.reaction_target(snapshots, "call", "dock/only-second")
        self.assertEqual((owner.spec.home, task), (second, "only-second"))
        self.assertEqual(quarterdeck.reaction_target(snapshots, "call", "dock/harbor-hold")[0].spec.home, self.home)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            quarterdeck.reaction_target(snapshots, "call", "dock/tide-note")

    def test_import_keeps_a_failed_delivery_for_retry_and_rejects_malformed_files(self) -> None:
        document = {"id": "react-0009", "item_id": "invented-home/harbor-hold", "kind": "call", "reaction": "hold",
                    "text": "", "at": "2026-10-12T10:00:00Z"}
        path = self.root / "reactions.json"
        path.write_text(json.dumps([document]), encoding="utf-8")
        with patch.dict(os.environ, {"QD_TEST_FAIL": "1"}):
            self.assertIn("skipped: react-0009", self.cli("reactions", "import", str(path)).stdout)
        self.assertEqual(self.review_state().delivered, {})
        self.assertIn("Delivered 1 of 1", self.cli("reactions", "import", str(path)).stdout)
        for name, content in {"not-json": "{", "wrong-shape": json.dumps({"reactions": "x"}),
                              "version": json.dumps({"version": 2, "reactions": []})}.items():
            with self.subTest(name):
                bad = self.root / f"{name}.json"
                bad.write_text(content, encoding="utf-8")
                self.cli("reactions", "import", str(bad), expect=2)

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the generated artifact")
    def test_artifact_sends_reactions_to_the_inbox_collection_and_opens_the_composer(self) -> None:
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        snapshot["reactions"] = [{"kind": "call", "item_id": "invented-home/tide-hold", "reaction": "hold",
                                  "text": "from the page", "at": "2000-01-02T00:00:00Z", "source": "page"}]
        snapshot["delivered"] = []
        page = quarterdeck.artifact_page(snapshot)
        harness = r"""
const assert = require('node:assert/strict');
const page = require('node:fs').readFileSync(0, 'utf8');
const payload = page.match(/<script type="application\/json" id="qd-snapshot">([\s\S]*?)<\/script>/)[1];
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  setAttribute(key, value) { this.attrs[key] = value; if (key === 'id') this.id = value; }
  getAttribute(key) { return this.attrs[key]; }
  appendChild(child) { this.children.push(child); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(tag) { return this.children.flatMap(c => [ ...(c.tag === tag ? [c] : []), ...c.querySelectorAll(tag) ]); }
}
const elements = Object.fromEntries(['qd-snapshot', 'root', 'title', 'generated', 'sync'].map(id => [id, new Element('div')]));
elements['qd-snapshot'].textContent = payload;
global.document = {createElement: tag => new Element(tag), getElementById: id => elements[id]};
const written = {};
const composed = [];
let inboxCallback;
const db = {
  collection: name => ({onSnapshot: callback => { if (name === 'qd-inbox') inboxCallback = callback; else callback({docs: []}); }}),
  doc: path => ({set: data => {
    attempts.push(path);
    if (nextSet) { const pending = nextSet; nextSet = null; return pending(); }
    written[path] = data; return Promise.resolve();
  }})
};
const attempts = [];
let nextSet = null;
let downloaded;
global.claude = {use: async name => name === 'db' ? db
  : name === 'comments' ? {openComposer: async target => { composed.push(target); return {opened: true}; }}
  : {save: async data => { downloaded = JSON.parse(data.data); }}};
global.window = {claude};
require('node:vm').runInThisContext(page.match(/<script>([\s\S]*?)<\/script>/)[1]);
const flush = async () => {for (let i = 0; i < 8; i++) await Promise.resolve();};
const cards = () => elements.root.querySelectorAll('article');
const card = title => cards().find(c => c.children.some(n => n.tag === 'h3' && n.textContent === title));
const button = (c, text) => c.querySelectorAll('button').find(b => b.textContent === text);
(async () => {
  await flush();
  const tide = card('Tide table');
  assert.ok(tide.textContent.includes('Latest reaction: Hold: from the page (from the local page'));
  const harbor = card('Quay colour');
  for (const label of ['Approve', 'Hold', 'Not now', 'Question', 'Send', 'Discuss with Firstmate']) assert.ok(button(harbor, label), label);
  button(harbor, 'Send').listeners.click();
  assert.equal(Object.keys(written).length, 0);  // nothing to send yet
  assert.ok(harbor.textContent.includes('Pick a reaction or write a reply first.'));
  button(harbor, 'Approve').listeners.click();
  const input = harbor.querySelectorAll('input')[0];
  input.value = '  Go  with teal '; input.listeners.input();
  button(harbor, 'Send').listeners.click();
  await flush();
  const paths = Object.keys(written);
  assert.equal(paths.length, 1);
  const data = written[paths[0]];
  assert.match(paths[0], /^qd-inbox\/[A-Za-z0-9._-]{8,64}$/);
  assert.equal(paths[0], 'qd-inbox/' + data.id);
  assert.deepEqual([data.kind, data.item_id, data.reaction, data.text, data.source, data.version],
    ['call', 'invented-home/harbor-hold', 'approve', 'Go with teal', 'artifact', 1]);
  assert.equal(data.task, undefined);
  assert.ok(!Number.isNaN(Date.parse(data.at)));
  assert.ok(card('Quay colour').textContent.includes('Latest reaction: Approve: Go with teal (from the artifact'));
  button(card('Quay colour'), 'Discuss with Firstmate').listeners.click();
  await flush();
  assert.equal(composed.length, 1);
  assert.ok(composed[0].element);
  const save = elements.root.querySelectorAll('button').find(b => b.textContent.startsWith('Download 1 recorded reaction'));
  save.listeners.click(); await flush();
  assert.equal(downloaded.version, 1);
  assert.deepEqual(downloaded.reactions.map(r => r.id), [data.id]);
  const exported = downloaded;
  const tideCard = () => card('Tide table');
  const sync = () => inboxCallback({docs: Object.values(written).map(d => ({data: () => d}))});
  button(tideCard(), 'Hold').listeners.click();
  tideCard().querySelectorAll('input')[0].value = 'wait'; tideCard().querySelectorAll('input')[0].listeners.input();
  let rejectSet;
  nextSet = () => new Promise((_, reject) => { rejectSet = reject; });
  button(tideCard(), 'Send').listeners.click();
  sync();  // a re-render while the send is pending keeps the composer locked
  assert.ok(button(tideCard(), 'Send').disabled && button(tideCard(), 'Approve').disabled && tideCard().querySelectorAll('input')[0].disabled);
  assert.ok(tideCard().textContent.includes('Sending…'));
  rejectSet(new Error('offline')); await flush();
  assert.ok(tideCard().textContent.includes('Could not send: offline'));
  assert.ok(!button(tideCard(), 'Send').disabled);
  assert.equal(tideCard().querySelectorAll('input')[0].value, 'wait');
  nextSet = () => Promise.reject(new Error('offline'));
  button(tideCard(), 'Send').listeners.click(); await flush();
  assert.equal(attempts.at(-1), attempts.at(-2));  // an unchanged retry reuses its id
  button(tideCard(), 'Not now').listeners.click();
  button(tideCard(), 'Send').listeners.click(); await flush();
  assert.notEqual(attempts.at(-1), attempts.at(-2));  // an edited reaction gets a new id
  assert.equal(written[attempts.at(-1)].reaction, 'not-now');
  const heading = c => c.children.find(n => n.tag === 'h3').textContent;
  const twice = cards().map(heading).find(title => cards().filter(c => heading(c) === title).length > 1);
  assert.ok(twice, 'an item shown on two cards');
  const [first, second] = cards().filter(c => heading(c) === twice);
  const firstInput = first.querySelectorAll('input')[0];
  const secondInput = second.querySelectorAll('input')[0];
  firstInput.value = 'Wait'; firstInput.listeners.input();
  secondInput.value = 'Proceed'; secondInput.listeners.input();
  assert.equal(firstInput.value, 'Proceed');  // every copy shows the draft Send would submit
  button(second, 'Question').listeners.click();
  assert.equal(button(first, 'Question').getAttribute('aria-pressed'), 'true');
  process.stdout.write(JSON.stringify(exported));
})().catch(error => {process.stderr.write(error.stack); process.exitCode = 1;});
"""
        result = subprocess.run(["node", "-e", harness], input=page, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        downloaded = self.root / "reactions.json"
        downloaded.write_text(result.stdout, encoding="utf-8")
        self.assertIn("Delivered 1 of 1", self.cli("reactions", "import", str(downloaded)).stdout)
        self.assertIn("Approve on call invented-home/harbor-hold (task harbor-hold)", self.logged()[5])
        self.assertIn("Go with teal", self.logged()[5])

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the generated artifact")
    def test_artifact_counts_cards_opens_details_and_reports_each_capability_state(self) -> None:
        snapshot = json.loads(self.cli("export", "--no-snapshot").stdout)
        def call(call_id: str, group: str, detail: str) -> dict[str, object]:
            return {"id": call_id, "title": f"Title {call_id}", "ask": "Ask?", "group": "Decide", "band": "desk",
                    "desk_group": group, "home": "invented-home", "project": "", "topic": "", "detail": detail,
                    "touched_at": None, "deferred": "", "sweep": False, "recommendation": "", "brief_url": "",
                    "links": [], "relevance": "", "relevance_reason": "", "related_to": []}
        snapshot["calls"] = [call("c1", "First heading", "Plain <b>text</b> detail"), call("c2", "Second heading", "")]
        snapshot["session"] = None
        snapshot["reactions"] = []
        snapshot["delivered"] = []
        page = quarterdeck.artifact_page(snapshot)
        harness = r"""
const assert = require('node:assert/strict');
const page = require('node:fs').readFileSync(0, 'utf8');
const payload = page.match(/<script type="application\/json" id="qd-snapshot">([\s\S]*?)<\/script>/)[1];
const script = page.match(/<script>([\s\S]*?)<\/script>/)[1];
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this._text = ''; this.open = false; this.disabled = false; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set className(value) { this.attrs.class = value; }
  setAttribute(key, value) { this.attrs[key] = value; if (key === 'id') this.id = value; }
  getAttribute(key) { return this.attrs[key]; }
  appendChild(child) { this.children.push(child); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  all(tag) { return this.children.flatMap(c => [ ...(c.tag === tag ? [c] : []), ...c.all(tag) ]); }
  querySelectorAll(tag) { return this.all(tag); }
}
function boot(use) {
  const elements = Object.fromEntries(['qd-snapshot', 'root', 'title', 'generated', 'sync'].map(id => [id, new Element('div')]));
  elements['qd-snapshot'].textContent = payload;
  global.document = {createElement: tag => new Element(tag), getElementById: id => elements[id], activeElement: null};
  global.window = use === undefined ? {} : {claude: {use}};
  require('node:vm').runInThisContext(script);
  return elements.root;
}
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const snaps = [];
const db = {collection: () => ({onSnapshot: cb => { snaps.push(cb); cb({docs: []}); }}), doc: () => ({set: () => Promise.resolve()})};
const comments = {openComposer: async () => ({opened: true})};
const buttons = (root, text) => root.all('button').filter(b => b.textContent === text);
const card = (root, title) => root.all('article').find(c => c.children.some(n => n.tag === 'h3' && n.textContent === title));
(async () => {
  // Late resolution: controls exist and are disabled at once, then light up.
  let release;
  const late = new Promise(resolve => { release = resolve; });
  let root = boot(name => late.then(() => name === 'db' ? db : comments));
  assert.ok(buttons(root, 'Approve').length >= 2 && buttons(root, 'Approve').every(b => b.disabled));
  assert.ok(buttons(root, 'Discuss with Firstmate').every(b => b.disabled));
  assert.ok(root.textContent.includes('Connecting…'));
  assert.ok(global.document.getElementById('sync').textContent.includes('Connecting'));
  // The desk badge counts the two cards, not the two sub-headings.
  const deskHead = root.all('section').find(s => s.id === 'desk').children[0];
  assert.equal(deskHead.all('span').find(s => s.attrs.class === 'count').textContent, '2');
  release(); await flush();
  assert.ok(buttons(root, 'Approve').every(b => !b.disabled));
  assert.ok(buttons(root, 'Discuss with Firstmate').every(b => !b.disabled));
  assert.ok(global.document.getElementById('sync').textContent.startsWith('Connected'));
  assert.equal(root.textContent.includes('see the note at the top'), false);

  // Details render as text, open, and stay open across a re-render.
  const first = card(root, 'Title c1');
  const detail = first.all('details')[0];
  assert.ok(detail && detail.textContent.includes('Plain <b>text</b> detail'));
  assert.equal(card(root, 'Title c2').all('details').length, 0);
  assert.equal(detail.all('b').length, 0);
  detail.open = true;
  const week = root.all('details').find(d => d.id === 'week');
  assert.ok(week && !week.open);
  week.open = true;
  snaps[0]({docs: []});  // a database snapshot re-renders the whole page
  const after = root.all('details');
  assert.ok(after.find(d => d.id === detail.id).open, 'call details stay open');
  assert.ok(after.find(d => d.id === 'week').open, 'group stays open');
  assert.equal(after.find(d => d.id === 'parked').open, false);

  // Resolving null: controls stay, disabled, with a one-line reason in the header and on the controls.
  root = boot(async () => null); await flush();
  assert.ok(buttons(root, 'Approve').length >= 2 && buttons(root, 'Approve').every(b => b.disabled));
  assert.ok(buttons(root, 'Discuss with Firstmate').every(b => b.disabled));
  assert.ok(global.document.getElementById('sync').textContent.startsWith('Read-only: the artifact database is not available'));
  assert.ok(root.textContent.includes('Reactions and Discuss unavailable, see the note at the top.'));
  assert.equal(root.textContent.includes('not available in this view'), false, 'cards do not repeat the full reason');

  // Only comments null.
  root = boot(async name => name === 'db' ? db : null); await flush();
  assert.ok(buttons(root, 'Approve').every(b => !b.disabled));
  assert.ok(buttons(root, 'Discuss with Firstmate').every(b => b.disabled));
  assert.ok(global.document.getElementById('sync').textContent.includes('Discuss with Firstmate is off'));
  assert.ok(root.textContent.includes('Discuss unavailable, see the note at the top.'));

  // A read error on one collection survives a later good snapshot on the other.
  const listeners = {};
  const flaky = {collection: name => ({onSnapshot: (cb, fail) => { listeners[name] = cb; if (name === 'qd-inbox') fail(new Error('denied')); else cb({docs: []}); }}),
                 doc: () => ({set: () => Promise.resolve()})};
  root = boot(async name => name === 'db' ? flaky : comments); await flush();
  assert.ok(global.document.getElementById('sync').textContent.includes('Could not read recorded reactions.'));
  Object.keys(listeners).filter(n => n !== 'qd-inbox').forEach(n => listeners[n]({docs: []}));
  assert.ok(global.document.getElementById('sync').textContent.includes('Could not read recorded reactions.'));

  // A rejected or missing runtime is also reported, never thrown.
  root = boot(async () => { throw new Error('boom'); }); await flush();
  assert.ok(global.document.getElementById('sync').textContent.includes('failed to load (boom)'));
  root = boot(undefined); await flush();
  assert.ok(global.document.getElementById('sync').textContent.startsWith('Read-only: open this page as a Claude artifact'));
  assert.ok(buttons(root, 'Approve').length >= 2);
})().catch(error => {process.stderr.write(error.stack); process.exitCode = 1;});
"""
        result = subprocess.run(["node", "-e", harness], input=page, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


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
